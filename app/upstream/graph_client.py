"""Microsoft Graph Copilot Chat API 客户端（HTTPX）。

只调用官方文档中的 3 个端点：
- POST /beta/copilot/conversations                      创建会话
- POST /beta/copilot/conversations/{id}/chat            同步聊天
- POST /beta/copilot/conversations/{id}/chatOverStream  流式聊天（SSE）

认证：调用方注入 token_provider（返回委派访问令牌）。
重试：仅在网络错误/429/5xx 且未产生任何流式输出前进行。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

from ..core.config import Settings
from ..core.errors import AdapterError, UpstreamError, extract_graph_error_message
from . import retry
from .sse_parser import parse_sse_lines

logger = logging.getLogger("m365_adapter.upstream")

TokenProvider = Callable[[], Awaitable[str]]


class GraphClient:
    def __init__(
        self,
        settings: Settings,
        token_provider: TokenProvider,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._token_provider = token_provider
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_seconds)
        )
        self._sem = asyncio.Semaphore(settings.upstream_concurrency)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def _base(self) -> str:
        return self._settings.graph_base_url.rstrip("/")

    async def _headers(self) -> dict[str, str]:
        token = await self._token_provider()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _raise_for(self, status: int, body: str) -> None:
        raise UpstreamError(status, extract_graph_error_message(status, body))

    # ---------- 创建会话 ----------
    async def create_conversation(self) -> str:
        url = f"{self._base}/copilot/conversations"
        resp = await self._request_with_retry("POST", url, json_body={})
        data = resp.json()
        conv_id = data.get("id")
        if not conv_id:
            raise UpstreamError(502, "Graph 创建会话响应缺少 id")
        logger.info("已创建 Copilot 会话 %s", conv_id)
        return conv_id

    # ---------- 同步聊天 ----------
    async def chat(self, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}/copilot/conversations/{conversation_id}/chat"
        resp = await self._request_with_retry("POST", url, json_body=payload)
        return resp.json()

    # ---------- 带重试的普通请求 ----------
    async def _request_with_retry(
        self, method: str, url: str, json_body: dict[str, Any]
    ) -> httpx.Response:
        max_retries = self._settings.upstream_max_retries
        attempt = 0
        while True:
            try:
                async with self._sem:
                    resp = await self._client.request(
                        method,
                        url,
                        json=json_body,
                        headers=await self._headers(),
                        timeout=self._settings.request_timeout_seconds,
                    )
            except Exception as exc:
                if retry.is_retryable_exception(exc) and attempt < max_retries:
                    await retry.sleep_before_retry(None, attempt, self._settings.retry_base_seconds)
                    attempt += 1
                    continue
                if isinstance(exc, AdapterError):
                    raise  # 认证/业务异常原样上抛，不包装为网络错误
                raise UpstreamError(502, f"上游网络错误: {type(exc).__name__}") from exc
            if resp.status_code < 400:
                return resp
            body = resp.text
            if retry.is_retryable_status(resp.status_code) and attempt < max_retries:
                await retry.sleep_before_retry(resp, attempt, self._settings.retry_base_seconds)
                attempt += 1
                continue
            self._raise_for(resp.status_code, body)

    # ---------- 流式聊天 ----------
    async def chat_stream_events(
        self, conversation_id: str, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        """产出每个 SSE 事件的解析后 JSON（copilotConversation 快照）。

        - 连接建立/收到 200 前的失败可按策略重试；
        - 一旦产出第一个事件，任何中断都不重试（避免重复内容）；
        - 调用方取消（asyncio.CancelledError）时 with 块退出，上游连接立即释放。
        """
        url = f"{self._base}/copilot/conversations/{conversation_id}/chatOverStream"
        max_retries = self._settings.upstream_max_retries
        attempt = 0
        started = False
        while True:
            try:
                async with self._sem:
                    async with self._client.stream(
                        "POST",
                        url,
                        json=payload,
                        headers=await self._headers(),
                        timeout=httpx.Timeout(self._settings.stream_timeout_seconds),
                    ) as resp:
                        if resp.status_code >= 400:
                            body = (await resp.aread()).decode("utf-8", errors="replace")
                            if (
                                not started
                                and retry.is_retryable_status(resp.status_code)
                                and attempt < max_retries
                            ):
                                await retry.sleep_before_retry(
                                    resp, attempt, self._settings.retry_base_seconds
                                )
                                attempt += 1
                                continue
                            self._raise_for(resp.status_code, body)
                        async for raw in parse_sse_lines(resp):
                            started = True
                            try:
                                yield json.loads(raw)
                            except ValueError:
                                logger.warning("无法解析的 SSE 事件载荷，已跳过（%d 字节）", len(raw))
                        return
            except asyncio.CancelledError:
                logger.info("客户端已断开，释放上游流连接")
                raise
            except Exception as exc:
                if isinstance(exc, AdapterError):
                    raise
                if not started and retry.is_retryable_exception(exc) and attempt < max_retries:
                    await retry.sleep_before_retry(None, attempt, self._settings.retry_base_seconds)
                    attempt += 1
                    continue
                raise UpstreamError(502, f"上游流式连接错误: {type(exc).__name__}") from exc
