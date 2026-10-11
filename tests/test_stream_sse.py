"""测试 3/4：SSE 流式输出增量文本正确、无重复、无静默截断。"""
import json

from conftest import AUTH, FULL_TEXT, chat_body, parse_sse_frames


async def test_stream_deltas(client):
    async with client.stream(
        "POST", "/v1/chat/completions", headers=AUTH, json=chat_body(stream=True)
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = await resp.aread()

    frames = parse_sse_frames(raw)
    assert frames[-1] == "[DONE]"

    chunks = [json.loads(f) for f in frames[:-1]]
    deltas = [c["choices"][0]["delta"] for c in chunks]
    # 拼接所有 content 增量 = 完整文本
    content = "".join(d.get("content", "") for d in deltas)
    assert content == FULL_TEXT
    # 增量而非快照：每个 content 都是严格新增部分（无重复文字）
    contents = [d.get("content", "") for d in deltas if d.get("content")]
    assert contents == ["你好", "，世界", "！"]
    # 第一个 chunk 带 role
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
    # 最后一个 chunk finish_reason=stop
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    # chunk 结构
    assert chunks[0]["object"] == "chat.completion.chunk"
    assert chunks[0]["model"] == "m365-copilot-chat"


async def test_stream_uses_stream_endpoint(client, graph):
    async with client.stream(
        "POST", "/v1/chat/completions", headers=AUTH, json=chat_body(stream=True)
    ) as resp:
        await resp.aread()
    stream_calls = [b for name, b in graph.calls if name == "stream"]
    assert len(stream_calls) == 1
    assert stream_calls[0]["message"]["text"] == "打个招呼"


async def test_stream_truncated_upstream(client, graph):
    """上游流中途 EOF（无最终事件）：不伪造完成，直接结束，不重复内容。"""
    import conftest
    truncated = b"data: " + json.dumps(
        {"id": "conv-0", "turnCount": 0, "messages": [{"id": "a1", "text": "只说半句"}]},
        ensure_ascii=False,
    ).encode() + b"\n\n"
    import httpx
    graph.chat_responses.append(
        httpx.Response(200, content=truncated, headers={"Content-Type": "text/event-stream"})
    )
    async with client.stream(
        "POST", "/v1/chat/completions", headers=AUTH, json=chat_body(stream=True)
    ) as resp:
        raw = await resp.aread()
    frames = parse_sse_frames(raw)
    chunks = [json.loads(f) for f in frames if f != "[DONE]"]
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert content == "只说半句"  # 不重放、不补全
