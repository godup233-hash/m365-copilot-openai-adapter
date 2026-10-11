"""GET /healthz — 无需鉴权的健康检查。"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    store = request.app.state.session_store
    return {"status": "ok", "active_sessions": len(store)}
