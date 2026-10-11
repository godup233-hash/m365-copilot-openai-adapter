"""测试 5/6：多轮会话延续、不同会话隔离。"""
from conftest import AUTH, chat_body


async def test_multi_turn_same_session(client, graph):
    body = chat_body(metadata={"session_id": "s-1"})
    r1 = await client.post("/v1/chat/completions", headers=AUTH, json=body)
    r2 = await client.post("/v1/chat/completions", headers=AUTH, json=body)
    assert r1.status_code == r2.status_code == 200
    creates = [c for c in graph.calls if c[0] == "create"]
    chats = [c for c in graph.calls if c[0] == "chat"]
    # 同一会话第二次复用 conversation_id，不新建
    assert len(creates) == 1
    assert len(chats) == 2


async def test_different_sessions_isolated(client, graph):
    await client.post("/v1/chat/completions", headers=AUTH, json=chat_body(metadata={"session_id": "s-1"}))
    await client.post("/v1/chat/completions", headers=AUTH, json=chat_body(metadata={"session_id": "s-2"}))
    creates = [c for c in graph.calls if c[0] == "create"]
    assert len(creates) == 2  # 不同 session_id → 不同上游会话


async def test_user_field_as_session_key(client, graph):
    await client.post("/v1/chat/completions", headers=AUTH, json=chat_body(user="alice"))
    await client.post("/v1/chat/completions", headers=AUTH, json=chat_body(user="bob"))
    creates = [c for c in graph.calls if c[0] == "create"]
    assert len(creates) == 2


async def test_default_bucket_shared(client, graph):
    # 无任何会话字段 → 单用户默认桶，共享一个上游会话
    await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    creates = [c for c in graph.calls if c[0] == "create"]
    assert len(creates) == 1


async def test_history_injected_only_on_first_turn(client, graph):
    body = chat_body(
        metadata={"session_id": "s-9"},
        messages=[
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "回复一"},
            {"role": "user", "content": "第二条"},
        ],
    )
    await client.post("/v1/chat/completions", headers=AUTH, json=chat_body(metadata={"session_id": "s-9"}))
    await client.post("/v1/chat/completions", headers=AUTH, json=body)
    chats = [b for name, b in graph.calls if name == "chat"]
    # 第二轮（is_new=False）：历史不重复注入 additionalContext
    second = chats[-1]
    contexts = second.get("additionalContext", [])
    assert not any("对话历史" in c["text"] for c in contexts)
