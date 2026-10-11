"""FastAPI 应用入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .api import routes_chat, routes_health, routes_models
from .auth import token_service
from .core.config import get_settings
from .core.errors import AdapterError, openai_error_response
from .core.logging import setup_logging
from .session.manager import SessionManager
from .session.store import SessionStore
from .upstream.graph_client import GraphClient

logger = logging.getLogger("m365_adapter")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, extra_secrets=[settings.adapter_api_key])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.graph_client.aclose()

    app = FastAPI(title="M365 Copilot OpenAI Adapter", docs_url=None, redoc_url=None,
                  lifespan=lifespan)

    # 上游组件（测试可通过 app.state 替换为 mock）
    async def _token_provider() -> str:
        return await token_service.get_access_token(settings)

    app.state.settings = settings
    app.state.graph_client = GraphClient(settings, _token_provider)
    app.state.session_store = SessionStore(ttl_seconds=settings.session_ttl_seconds)
    app.state.session_manager = SessionManager(
        settings, app.state.session_store, app.state.graph_client
    )

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit():
            if int(content_length) > settings.max_request_bytes:
                return openai_error_response(413, "请求体超过大小限制", code="request_too_large")
        return await call_next(request)

    @app.exception_handler(AdapterError)
    async def adapter_error_handler(request: Request, exc: AdapterError):
        return exc.to_response()

    app.include_router(routes_health.router)
    app.include_router(routes_models.router)
    app.include_router(routes_chat.router)

    return app


app = create_app()
