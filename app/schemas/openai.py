"""OpenAI 兼容请求/响应模型（宽松模式：容忍 Sub2API 注入与客户端扩展字段）。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = None  # str 或 [{"type":"text","text":...}, ...]
    name: str | None = None

    def text(self) -> str:
        """把 content 拍平成纯文本（多模态 parts 只取 text）。"""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts = []
            for item in self.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "\n".join(p for p in parts if p)
        return ""


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")  # 容忍 stream_options 等扩展字段

    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    user: str | None = None
    metadata: dict[str, Any] | None = None

    # 以下为 Copilot 不支持的参数：仅声明以便识别，实际不生效（README 有完整清单）
    tools: Any = None
    functions: Any = None

    def last_user_message(self) -> ChatMessage | None:
        for msg in reversed(self.messages):
            if msg.role == "user":
                return msg
        return None

    def system_texts(self) -> list[str]:
        return [
            m.text()
            for m in self.messages
            if m.role in ("system", "developer") and m.text().strip()
        ]

    def history_pairs(self) -> list[ChatMessage]:
        """最后一条 user 消息之前的 user/assistant 历史。"""
        msgs = self.messages
        last_user_idx = max(i for i, m in enumerate(msgs) if m.role == "user")
        return [m for m in msgs[:last_user_idx] if m.role in ("user", "assistant")]


class ChatCompletionResponseMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatCompletionResponseMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    # usage 刻意省略：Graph 不提供 token 计数，不伪造
