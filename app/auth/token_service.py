"""令牌获取服务：在线程池中执行同步 MSAL 调用，避免阻塞事件循环。"""
from __future__ import annotations

import asyncio
import logging

from ..core.config import Settings
from ..core.errors import AuthRequiredError
from . import msal_client

logger = logging.getLogger("m365_adapter.auth")


async def get_access_token(settings: Settings) -> str:
    try:
        return await asyncio.to_thread(msal_client.acquire_token_silent, settings)
    except AuthRequiredError:
        raise
    except Exception as exc:  # 网络/解析等异常统一归为认证不可用
        logger.error("获取访问令牌失败: %s", type(exc).__name__)
        raise AuthRequiredError(f"获取访问令牌失败（{type(exc).__name__}），请检查网络后重新 login") from exc
