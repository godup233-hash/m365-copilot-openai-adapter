"""流式聊天：Graph SSE 累积快照 → OpenAI chat.completion.chunk 增量流。

转换规则（设计文档 6.3）：
1. Graph 事件是"累积快照"，助手消息 id 跨事件稳定 → 按 id 跟踪、按文本前缀差分，
   只输出新增后缀，绝不重发已输出内容；
2. 空 messages 的心跳事件忽略；
3. turnCount>=1 为最终事件 → 发 finish chunk + [DONE]；
4. 流中途 EOF（未见最终事件）→ 不重放、不自动重发请求，记 ERROR 后结束；
5. 客户端断开 → 取消传播，上游连接立即释放；
6. usage 不发（Graph 无 token 计数）；stream_options.include_usage 被容忍但忽略。

结构说明：prepare_stream() 在返回 HTTP 响应前完成鉴权/会话/载荷预检
（错误可映射为正常 HTTP 状态码）；stream_pump() 才是 SSE 生成器。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from ..core.config import Settings
from ..core.errors import AdapterError, UpstreamError
from ..schemas.graph import is_final_snapshot, pick_assistant_message
from ..schemas.openai import ChatCompletionRequest
from ..session.manager import SessionManager
from ..session.store import SessionEntry
from ..upstream.sse_parser import DONE_FRAME, format_sse
from .chat_service import build_upstream_payload, validate_request

logger = logging.getLogger("m365_adapter.stream")


def _chunk(chunk_id: str, created: int, model: str, delta: dict[str, Any],
           finish_reason: str | None = None) -> bytes:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return format_sse(json.dumps(payload, ensure_ascii=False))


def _error_frame(exc: AdapterError) -> bytes:
    payload = {
        "error": {
            "message": str(exc),
            "type": exc.err_type,
            "code": exc.code,
        }
    }
    return format_sse(json.dumps(payload, ensure_ascii=False))


async def prepare_stream(
    request: ChatCompletionRequest,
    settings: Settings,
    session_manager: SessionManager,
    session_key: str,
) -> tuple[SessionEntry, bool, dict[str, Any], str]:
    """流式预检：参数校验 + 会话获取（触发认证）。错误在此抛出可映射为 HTTP 响应。"""
    validate_request(request)
    entry, is_new = await session_manager.get_or_create(session_key)
    payload, user_text = build_upstream_payload(request, settings, is_new)
    return entry, is_new, payload, user_text


async def stream_pump(
    entry: SessionEntry,
    payload: dict[str, Any],
    user_text: str,
    model: str,
    session_manager: SessionManager,
) -> AsyncIterator[bytes]:
    graph = session_manager._graph
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    async with entry.lock:  # 同会话串行
        try:
            try:
                async for frame in _pump(graph, entry.conversation_id, payload,
                                         user_text, chunk_id, created, model):
                    yield frame
            except UpstreamError as exc:
                if session_manager.is_conversation_missing_error(exc):
                    # 尚未产出任何内容时的会话失效 → 按策略恢复并重发一次
                    new_entry = await session_manager.handle_session_not_found(entry)
                    entry.conversation_id = new_entry.conversation_id
                    async for frame in _pump(graph, new_entry.conversation_id, payload,
                                             user_text, chunk_id, created, model):
                        yield frame
                else:
                    raise
            entry.turn_count += 1
        except AdapterError as exc:
            # 响应已开始，只能以 SSE 错误帧告知客户端
            yield _error_frame(exc)
            yield DONE_FRAME


async def _pump(
    graph,
    conversation_id: str,
    payload: dict[str, Any],
    user_text: str,
    chunk_id: str,
    created: int,
    model: str,
) -> AsyncIterator[bytes]:
    emitted = ""
    assistant_id: str | None = None
    sent_role = False
    final_seen = False

    async for snapshot in graph.chat_stream_events(conversation_id, payload):
        candidate = pick_assistant_message(
            snapshot.get("messages") or [], user_text, assistant_id
        )
        if candidate is not None:
            if assistant_id is None:
                assistant_id = candidate.get("id")
            text = candidate.get("text") or ""
            if text.startswith(emitted):
                delta = text[len(emitted):]
            else:
                # 快照回退/改写：不重发，记警告跳过（防重复输出）
                if text != emitted:
                    logger.warning("快照文本非前缀演进，已跳过该事件（防重复）")
                delta = ""
            if delta:
                if not sent_role:
                    sent_role = True
                    yield _chunk(chunk_id, created, model, {"role": "assistant", "content": delta})
                else:
                    yield _chunk(chunk_id, created, model, {"content": delta})
                emitted = text
        if is_final_snapshot(snapshot):
            final_seen = True
            if not sent_role:
                sent_role = True
                yield _chunk(chunk_id, created, model, {"role": "assistant"})
            yield _chunk(chunk_id, created, model, {}, finish_reason="stop")
            break

    if not final_seen:
        # 流被截断：不重放请求（可能已被微软接受，重发会重复执行），仅记录
        logger.error("上游流在未收到最终事件时结束（会话=%s），输出可能不完整", conversation_id)
    yield DONE_FRAME
