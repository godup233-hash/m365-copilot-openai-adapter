"""Graph copilotConversation 相关的轻量解析工具（不做全量建模）。"""
from __future__ import annotations

from typing import Any


def pick_assistant_message(
    messages: list[dict[str, Any]],
    user_text: str,
    known_assistant_id: str | None = None,
) -> dict[str, Any] | None:
    """从会话快照的 messages 数组中挑出助手消息。

    快照规律（官方示例）：
    - 中间事件：messages=[助手消息(部分或完整)] 或 []
    - 最终事件：messages=[用户消息回显, 助手完整消息]
    助手消息 id 跨事件稳定。
    """
    if not messages:
        return None
    if known_assistant_id:
        for m in messages:
            if m.get("id") == known_assistant_id:
                return m
        return None
    if len(messages) >= 2:
        return messages[-1]
    only = messages[0]
    if (only.get("text") or "") == user_text:
        return None
    return only


def is_final_snapshot(snapshot: dict[str, Any]) -> bool:
    """最终事件判定：turnCount 递增（中间事件恒为 0）。"""
    try:
        return int(snapshot.get("turnCount") or 0) >= 1
    except (TypeError, ValueError):
        return False
