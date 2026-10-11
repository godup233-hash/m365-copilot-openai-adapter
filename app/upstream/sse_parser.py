"""Graph SSE 解析：按 SSE 规范切分事件，输出每个事件的 data 载荷文本。

Graph chatOverStream 的每个事件是一个完整 copilotConversation JSON 快照
（官方示例为多行 pretty JSON，实际响应可能是单行 data 或多行 data 拼接，
两者都兼容）。忽略 id:/event:/注释行。
"""
from __future__ import annotations

from typing import AsyncIterator

import httpx


async def parse_sse_lines(resp: httpx.Response) -> AsyncIterator[str]:
    buf: list[str] = []
    async for line in resp.aiter_lines():
        if line == "":
            if buf:
                yield "\n".join(buf)
                buf = []
            continue
        if line.startswith(":"):  # SSE 注释/心跳
            continue
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
        # id: / event: / retry: 行忽略
    if buf:
        yield "\n".join(buf)


def format_sse(payload: str) -> bytes:
    """生成 OpenAI 风格 SSE 帧。"""
    return f"data: {payload}\n\n".encode("utf-8")


DONE_FRAME = b"data: [DONE]\n\n"
