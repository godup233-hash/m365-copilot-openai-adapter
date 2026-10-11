"""非流式聊天：OpenAI messages → Graph chat → OpenAI chat.completion。"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..core.config import Settings
from ..core.errors import AdapterError, UpstreamError
from ..schemas.graph import pick_assistant_message
from ..schemas.openai import ChatCompletionRequest
from ..session.manager import SessionManager
from ..session.store import SessionEntry

logger = logging.getLogger("m365_adapter.chat")


def build_upstream_payload(
    request: ChatCompletionRequest, settings: Settings, is_new_session: bool
) -> tuple[dict[str, Any], str]:
    """构造 Graph chat 请求体，返回 (payload, user_text)。

    映射策略（设计文档 6.1）：
    - 最后一条 user 消息 → message.text
    - system/developer 消息 → additionalContext（系统指令）
    - 新会话首轮：历史 user/assistant → additionalContext（对话历史，受配置开关控制）
    - 已有会话：历史不重复发送（Copilot 会话内自带上下文）
    """
    last_user = request.last_user_message()
    if last_user is None or not last_user.text().strip():
        raise AdapterError("messages 中缺少非空的 user 消息", param="messages")

    user_text = last_user.text()
    additional_context: list[dict[str, str]] = []

    for text in request.system_texts():
        additional_context.append({"text": f"[系统指令]\n{text}"})

    if is_new_session and settings.inject_history_on_first_turn:
        history = request.history_pairs()
        if history:
            lines = []
            for m in history:
                role = "用户" if m.role == "user" else "助手"
                if m.text().strip():
                    lines.append(f"{role}: {m.text()}")
            if lines:
                additional_context.append(
                    {"text": "[对话历史]（仅供上下文参考，请只回应最后一条新消息）\n" + "\n".join(lines)}
                )

    payload: dict[str, Any] = {
        "message": {"text": user_text},
        "locationHint": {"timeZone": settings.default_timezone},
    }
    if additional_context:
        payload["additionalContext"] = additional_context
    return payload, user_text


def validate_request(request: ChatCompletionRequest) -> None:
    if request.tools or request.functions:
        raise AdapterError(
            "Copilot Chat API 不支持工具调用（tools/functions），请移除后重试",
            param="tools",
        )


async def handle_chat_completion(
    request: ChatCompletionRequest,
    settings: Settings,
    session_manager: SessionManager,
    session_key: str,
) -> dict[str, Any]:
    validate_request(request)
    entry, is_new = await session_manager.get_or_create(session_key)

    async with entry.lock:  # 同会话串行
        payload, user_text = build_upstream_payload(request, settings, is_new)
        try:
            data = await _chat_with_recovery(session_manager, entry, payload, settings)
        except UpstreamError:
            raise
        entry.turn_count += 1

    assistant = pick_assistant_message(data.get("messages") or [], user_text)
    content = (assistant or {}).get("text") or ""
    if not content:
        logger.error("上游响应缺少助手消息文本，会话=%s", entry.conversation_id)
        raise UpstreamError(502, "上游响应缺少助手消息内容")

    return {
        "id": f"chatcmpl-{(assistant or {}).get('id') or uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        # usage 刻意省略：Graph 不提供 token 计数，不伪造
    }


async def _chat_with_recovery(
    session_manager: SessionManager,
    entry: SessionEntry,
    payload: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    graph = session_manager._graph
    try:
        return await graph.chat(entry.conversation_id, payload)
    except UpstreamError as exc:
        if session_manager.is_conversation_missing_error(exc):
            # 会话失效：按策略恢复；auto_new 时用新会话重发一次（历史注入已由
            # 调用方在 is_new_session 维度决定——恢复场景同样按"新会话首轮"处理，
            # 但此处 payload 已按旧 is_new 构造，保守起见仅当 auto_new 时重发）。
            new_entry = await session_manager.handle_session_not_found(entry)
            entry.conversation_id = new_entry.conversation_id
            entry.created_at = new_entry.created_at
            return await graph.chat(new_entry.conversation_id, payload)
        raise
