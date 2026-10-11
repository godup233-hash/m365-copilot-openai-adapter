"""测试 2：非流式请求与响应映射正确。"""
import json

from conftest import AUTH, FULL_TEXT, USER_TEXT, chat_body


async def test_sync_chat_response_shape(client):
    resp = await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["id"].startswith("chatcmpl-")
    assert data["model"] == "m365-copilot-chat"
    assert isinstance(data["created"], int)
    assert len(data["choices"]) == 1
    choice = data["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == FULL_TEXT
    assert choice["finish_reason"] == "stop"
    assert "usage" not in data  # 不伪造 token 用量


async def test_upstream_payload_mapping(client, graph):
    await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    chat_calls = [b for name, b in graph.calls if name == "chat"]
    assert len(chat_calls) == 1
    payload = chat_calls[0]
    # 最后一条 user 消息 → message.text
    assert payload["message"]["text"] == USER_TEXT
    # locationHint 必填
    assert payload["locationHint"]["timeZone"] == "Asia/Shanghai"
    # system 消息 → additionalContext
    contexts = payload.get("additionalContext", [])
    assert any("系统指令" in c["text"] and "你是一个助手" in c["text"] for c in contexts)


async def test_tools_rejected(client):
    body = chat_body(tools=[{"type": "function", "function": {"name": "f"}}])
    resp = await client.post("/v1/chat/completions", headers=AUTH, json=body)
    assert resp.status_code == 400
    assert "工具" in resp.json()["error"]["message"] or "tools" in resp.json()["error"]["message"]


async def test_empty_user_message_rejected(client):
    resp = await client.post(
        "/v1/chat/completions", headers=AUTH,
        json={"model": "m365-copilot-chat", "messages": [{"role": "system", "content": "hi"}]},
    )
    assert resp.status_code == 400


async def test_invalid_json(client):
    resp = await client.post(
        "/v1/chat/completions", headers={**AUTH, "Content-Type": "application/json"},
        content=b"{not json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"


async def test_stream_options_tolerated(client):
    # Sub2API 会注入 stream_options.include_usage，Adapter 必须容忍
    resp = await client.post(
        "/v1/chat/completions", headers=AUTH,
        json=chat_body(stream_options={"include_usage": True}),
    )
    assert resp.status_code == 200
