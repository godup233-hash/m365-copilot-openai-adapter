"""测试共享 fixtures：环境变量、伪造 Graph 上游（HTTPX MockTransport）、测试 app。

所有测试均为 Mock 档——不访问真实 Microsoft Graph，不标记任何真实链路结论。
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.core.errors import AuthRequiredError
from app.main import create_app
from app.session.manager import SessionManager
from app.session.store import SessionStore
from app.upstream.graph_client import GraphClient

AUTH = {"Authorization": "Bearer test-key"}
GRAPH_BASE = "https://graph.microsoft.com/beta"

USER_TEXT = "打个招呼"
ASSISTANT_ID = "a1"
FULL_TEXT = "你好，世界！"

SYNC_RESPONSE = {
    "id": "conv-0",
    "createdDateTime": "2026-10-12T00:00:00Z",
    "displayName": USER_TEXT,
    "state": "active",
    "turnCount": 1,
    "messages": [
        {"id": "u1", "text": USER_TEXT, "createdDateTime": "2026-10-12T00:00:00Z",
         "@odata.type": "#microsoft.graph.copilotConversationResponseMessage"},
        {"id": ASSISTANT_ID, "text": FULL_TEXT, "createdDateTime": "2026-10-12T00:00:01Z",
         "@odata.type": "#microsoft.graph.copilotConversationResponseMessage"},
    ],
}

# 累积快照流：部分文本 → 更长文本 → 空心跳 → 最终事件
SSE_EVENTS = [
    {"id": "conv-0", "displayName": "Intermediate Conversation Update", "state": "active",
     "turnCount": 0, "messages": [{"id": ASSISTANT_ID, "text": "你好"}]},
    {"id": "conv-0", "displayName": "Intermediate Conversation Update", "state": "active",
     "turnCount": 0, "messages": [{"id": ASSISTANT_ID, "text": "你好，世界"}]},
    {"id": "conv-0", "displayName": "Intermediate Conversation Update", "state": "active",
     "turnCount": 0, "messages": []},
    {"id": "conv-0", "displayName": USER_TEXT, "state": "active", "turnCount": 1,
     "messages": [
         {"id": "u1", "text": USER_TEXT},
         {"id": ASSISTANT_ID, "text": FULL_TEXT},
     ]},
]
SSE_BYTES = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in SSE_EVENTS).encode()


async def fake_token() -> str:
    return "fake-access-token"


async def failing_token() -> str:
    raise AuthRequiredError()


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ADAPTER_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_CLIENT_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("TOKEN_CACHE_PATH", str(tmp_path / "token_cache.bin"))
    monkeypatch.setenv("RETRY_BASE_SECONDS", "0.01")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


class GraphScript:
    """可编程的 Graph 上游：按路径分发，支持排队响应。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._conv_counter = 0
        self.chat_responses: list[httpx.Response] = []  # 非空时按序弹出

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                pass
        if path.rstrip("/") == "/beta/copilot/conversations" and request.method == "POST":
            self._conv_counter += 1
            self.calls.append(("create", body))
            return httpx.Response(201, json={
                "id": f"conv-{self._conv_counter}",
                "createdDateTime": "2026-10-12T00:00:00Z",
                "displayName": "", "state": "active", "turnCount": 0,
            })
        if path.endswith("/chat") and request.method == "POST":
            self.calls.append(("chat", body))
            if self.chat_responses:
                return self.chat_responses.pop(0)
            return httpx.Response(200, json=SYNC_RESPONSE)
        if path.endswith("/chatOverStream") and request.method == "POST":
            self.calls.append(("stream", body))
            if self.chat_responses:
                return self.chat_responses.pop(0)
            return httpx.Response(
                200, content=SSE_BYTES, headers={"Content-Type": "text/event-stream"}
            )
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": "unknown"}})


@pytest.fixture
def graph() -> GraphScript:
    return GraphScript()


def build_test_app(settings: Settings, graph: GraphScript, token_provider=fake_token):
    app = create_app()
    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(graph.handler))
    app.state.graph_client = GraphClient(settings, token_provider, client=mock_http)
    app.state.session_store = SessionStore(ttl_seconds=settings.session_ttl_seconds)
    app.state.session_manager = SessionManager(
        settings, app.state.session_store, app.state.graph_client
    )
    return app


@pytest.fixture
def app(settings, graph):
    return build_test_app(settings, graph)


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def chat_body(**overrides):
    body = {
        "model": "m365-copilot-chat",
        "messages": [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": USER_TEXT},
        ],
    }
    body.update(overrides)
    return body


def parse_sse_frames(raw: bytes) -> list[str]:
    """提取 SSE 流里所有 data 载荷。"""
    frames = []
    for block in raw.decode().split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            frames.append(block[5:].strip())
    return frames
