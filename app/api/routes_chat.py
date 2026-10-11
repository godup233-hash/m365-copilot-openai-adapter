"""POST /v1/chat/completions — 非流式 + SSE 流式。

注意：本项目刻意不实现 /v1/responses——Sub2API 的能力探测依赖该路径返回
干净的 404 来落标"不支持 Responses"，从而走 chat/completions 直转路径。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from ..core.config import Settings, get_settings
from ..core.errors import AdapterError, openai_error_response
from ..schemas.openai import ChatCompletionRequest
from ..services import chat_service, stream_service
from ..session.manager import resolve_session_key
from .deps import get_session_manager, require_api_key

logger = logging.getLogger("m365_adapter.api.chat")

router = APIRouter()


@router.post("/v1/chat/completions", dependencies=[Depends(require_api_key)])
async def chat_completions(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    try:
        body = await request.json()
    except Exception:
        return openai_error_response(400, "请求体不是合法 JSON")

    try:
        chat_req = ChatCompletionRequest.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", [])) or "body"
        return openai_error_response(
            400, f"请求参数校验失败: {loc}: {first.get('msg', 'invalid')}"
        )

    session_manager = get_session_manager(request)
    session_key = resolve_session_key(chat_req)

    try:
        if chat_req.stream:
            # 预检（鉴权/会话/载荷）在返回响应前完成，错误可映射为正常 HTTP 状态码
            entry, _is_new, payload, user_text = await stream_service.prepare_stream(
                chat_req, settings, session_manager, session_key
            )
            return StreamingResponse(
                stream_service.stream_pump(entry, payload, user_text, chat_req.model, session_manager),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        result = await chat_service.handle_chat_completion(
            chat_req, settings, session_manager, session_key
        )
        return JSONResponse(content=result)
    except AdapterError as exc:
        return exc.to_response()
