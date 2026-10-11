"""共享依赖：Adapter API Key 校验 + 会话管理器获取。"""
from __future__ import annotations

import secrets

from fastapi import Depends, Request

from ..core.config import Settings, get_settings
from ..core.errors import AdapterError
from ..session.manager import SessionManager


async def require_api_key(
    request: Request, settings: Settings = Depends(get_settings)
) -> None:
    expected = settings.adapter_api_key
    if not expected:
        raise ServerMisconfigured()
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("缺少 Authorization: Bearer <api_key>")
    if not secrets.compare_digest(token, expected):
        raise Unauthorized("API Key 无效")


class Unauthorized(AdapterError):
    status = 401
    err_type = "authentication_error"
    code = "invalid_api_key"


class ServerMisconfigured(AdapterError):
    status = 500
    err_type = "server_error"
    code = "misconfigured"

    def __init__(self) -> None:
        super().__init__("服务端未配置 ADAPTER_API_KEY")


def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager
