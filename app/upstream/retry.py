"""重试策略：指数退避 + 随机抖动 + 最大次数，尊重 Retry-After。

只用于"请求尚未产生任何下游输出"的阶段（连接失败、429、临时 5xx）。
一旦开始向下游输出流式内容，绝不重试——避免重复提交与内容重复。
"""
from __future__ import annotations

import asyncio
import logging
import random

import httpx

logger = logging.getLogger("m365_adapter.upstream.retry")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)


def is_retryable_exception(exc: BaseException) -> bool:
    return isinstance(exc, _RETRYABLE_EXC)


def is_retryable_status(status: int) -> bool:
    return status in RETRYABLE_STATUS


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(float(raw), 60.0)  # 上限 60s，防止上游要求超长等待
    except ValueError:
        return None


async def sleep_before_retry(resp: httpx.Response | None, attempt: int, base: float) -> None:
    if resp is not None:
        ra = _retry_after_seconds(resp)
        if ra is not None:
            logger.info("上游要求 Retry-After=%ss（第 %d 次重试）", ra, attempt + 1)
            await asyncio.sleep(ra)
            return
    delay = base * (2 ** attempt) + random.uniform(0, base)
    delay = min(delay, 30.0)
    logger.info("退避 %.2fs 后重试（第 %d 次）", delay, attempt + 1)
    await asyncio.sleep(delay)
