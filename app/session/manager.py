"""会话管理：session_key 解析、会话创建、失效恢复。

会话键来源（按优先级，依据 Sub2API 源码核实的透传行为——自定义 header 不透传，
只有请求体字段可用）：
1. metadata.session_id（客户端显式设置）
2. user 字段（OpenAI 标准字段）
3. 都没有 → 单用户默认桶 "default"
"""
from __future__ import annotations

import hashlib
import logging

from ..core.config import Settings
from ..core.errors import SessionExpiredError, UpstreamError
from ..schemas.openai import ChatCompletionRequest
from ..upstream.graph_client import GraphClient
from .store import SessionEntry, SessionStore

logger = logging.getLogger("m365_adapter.session")

DEFAULT_SESSION_KEY = "default"


def resolve_session_key(request: ChatCompletionRequest) -> str:
    raw = None
    if request.metadata:
        candidate = request.metadata.get("session_id")
        if isinstance(candidate, str) and candidate.strip():
            raw = candidate.strip()
    if raw is None and request.user and request.user.strip():
        raw = request.user.strip()
    if raw is None:
        return DEFAULT_SESSION_KEY
    # 哈希后再做 key：避免日志/存储里出现客户端原始标识，长度也可控
    return "u_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class SessionManager:
    def __init__(self, settings: Settings, store: SessionStore, graph: GraphClient) -> None:
        self._settings = settings
        self._store = store
        self._graph = graph

    async def get_or_create(self, session_key: str) -> tuple[SessionEntry, bool]:
        """返回 (会话, 是否新建)。"""
        entry = self._store.get(session_key)
        if entry is not None:
            self._store.touch(entry)
            return entry, False
        conversation_id = await self._graph.create_conversation()
        entry = self._store.set(session_key, conversation_id)
        return entry, True

    async def handle_session_not_found(self, entry: SessionEntry) -> SessionEntry:
        """上游会话失效后的恢复策略。

        off      → 抛 SessionExpiredError，让客户端换 session_id 重开（不静默丢历史）
        auto_new → 本地丢弃映射并新建会话（有损：历史需由调用方重新注入）
        """
        self._store.invalidate(entry.session_key)
        if self._settings.session_recovery == "auto_new":
            logger.warning("会话 %s 已失效，按 auto_new 策略新建会话", entry.session_key)
            conversation_id = await self._graph.create_conversation()
            return self._store.set(entry.session_key, conversation_id)
        raise SessionExpiredError(entry.session_key)

    @staticmethod
    def is_conversation_missing_error(exc: UpstreamError) -> bool:
        return exc.status == 404
