"""测试 8：429 与临时 5xx 的重试处理（指数退避 + Retry-After + 上限）。"""
import httpx
from conftest import AUTH, chat_body


async def test_retry_on_429_then_success(client, graph):
    graph.chat_responses.extend([
        httpx.Response(429, json={"error": {"code": "TooManyRequests", "message": "slow down"}},
                       headers={"Retry-After": "0"}),
        httpx.Response(200, json={
            "id": "conv-0", "turnCount": 1,
            "messages": [{"id": "u1", "text": "打个招呼"}, {"id": "a1", "text": "恢复了"}],
        }),
    ])
    resp = await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "恢复了"
    chats = [c for c in graph.calls if c[0] == "chat"]
    assert len(chats) == 2  # 重试了 1 次


async def test_retry_exhausted_on_persistent_500(client, graph):
    graph.chat_responses.extend([httpx.Response(500, json={"error": {"code": "Internal", "message": "boom"}})] * 4)
    resp = await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    assert resp.status_code == 500 or resp.status_code == 502
    assert resp.json()["error"]["type"] == "upstream_error"


async def test_non_retryable_400_no_retry(client, graph):
    graph.chat_responses.append(
        httpx.Response(400, json={"error": {"code": "BadRequest", "message": "bad message"}})
    )
    resp = await client.post("/v1/chat/completions", headers=AUTH, json=chat_body())
    assert resp.status_code == 400
    chats = [c for c in graph.calls if c[0] == "chat"]
    assert len(chats) == 1  # 确定性错误不重试
