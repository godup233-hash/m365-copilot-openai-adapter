"""GET /v1/models — 返回实际支持的模型别名。

该名称仅是兼容客户端使用的别名，不代表可切换微软内部模型
（Copilot Chat API 没有模型选择能力）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ..core.config import Settings, get_settings
from .deps import require_api_key

router = APIRouter()


@router.get("/v1/models", dependencies=[Depends(require_api_key)])
async def list_models(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": settings.model_alias,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "m365-copilot",
            }
        ],
    }
