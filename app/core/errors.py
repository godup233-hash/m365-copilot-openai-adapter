"""OpenAI 风格错误与内部异常类型。

设计约束（依据 Sub2API 源码核实）：Sub2API 只对上游 400 系错误透传真实
message，401/403 会被吞成 502。因此业务错误尽量用 400 系返回。
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


def openai_error_response(
    status: int,
    message: str,
    err_type: str = "invalid_request_error",
    code: str | None = None,
    param: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "message": message,
                "type": err_type,
                "param": param,
                "code": code,
            }
        },
    )


class AdapterError(Exception):
    """可映射为 OpenAI 风格响应的业务异常。"""

    status: int = 400
    err_type: str = "invalid_request_error"
    code: str | None = None

    def __init__(self, message: str = "", param: str | None = None) -> None:
        self.param = param
        super().__init__(message)

    def to_response(self) -> JSONResponse:
        return openai_error_response(self.status, str(self), self.err_type, self.code, self.param)


class AuthRequiredError(AdapterError):
    """微软登录失效/未登录，需要重新执行 login。"""

    status = 401
    err_type = "authentication_error"
    code = "auth_required"

    def __init__(self, message: str = "Microsoft 登录失效或未完成登录，请执行 `m365-adapter login` 重新认证") -> None:
        super().__init__(message)


class UpstreamError(AdapterError):
    """Microsoft Graph 上游错误。"""

    def __init__(self, status: int, message: str, code: str | None = None) -> None:
        self.status = status if 400 <= status < 600 else 502
        self.err_type = "upstream_error" if self.status >= 500 else "invalid_request_error"
        if self.status == 401:
            self.err_type = "authentication_error"
        if self.status == 429:
            self.err_type = "rate_limit_error"
        self.code = code
        super().__init__(message)


class SessionExpiredError(AdapterError):
    """上游会话不存在/已失效，且未开启自动恢复。"""

    status = 400
    code = "session_expired"

    def __init__(self, session_key: str) -> None:
        super().__init__(
            f"上游会话已失效（session={session_key}）。请更换/清空 session_id 重新开始会话，"
            "或将 SESSION_RECOVERY 设为 auto_new 允许有损自动恢复。"
        )


def extract_graph_error_message(status: int, body: str) -> str:
    """从 Graph 错误体提取可读信息（不携带任何凭据）。"""
    import json

    try:
        data = json.loads(body)
        err = data.get("error") or {}
        code = err.get("code") or ""
        msg = err.get("message") or ""
        if code or msg:
            return f"Graph {status} {code}: {msg}".strip()
    except (ValueError, AttributeError):
        pass
    return f"Graph upstream error: HTTP {status}"
