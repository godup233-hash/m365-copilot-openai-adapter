"""MSAL 公共客户端 + 受保护的持久化 Token Cache。

认证方式：设备代码流（Device Code Flow），需要：
- Entra ID 应用注册（公共客户端，开启 Allow public client flows）
- 管理员同意以下 7 个 Graph 委派权限（Chat API 要求全部具备）
- 登录账号必须是带 M365 Copilot 许可证的工作/学校账号（个人账号不支持）

令牌只写入 token cache 文件（0600），永不写日志。
"""
from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import msal

from ..core.config import Settings
from ..core.errors import AuthRequiredError

logger = logging.getLogger("m365_adapter.auth")

# Chat API 要求的 7 个委派权限（缺一不可，见官方文档）
SCOPES = [
    "Sites.Read.All",
    "Mail.Read",
    "People.Read.All",
    "OnlineMeetingTranscript.Read.All",
    "Chat.Read",
    "ChannelMessage.Read.All",
    "ExternalItem.Read.All",
]


def _cache_path(settings: Settings) -> Path:
    return Path(settings.token_cache_path).expanduser()


def _load_cache(settings: Settings) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    path = _cache_path(settings)
    if path.exists():
        try:
            cache.deserialize(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("token cache 读取失败，将视为未登录: %s", type(exc).__name__)
    return cache


def _save_cache(cache: msal.SerializableTokenCache, settings: Settings) -> None:
    if not cache.has_state_changed:
        return
    path = _cache_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)  # 0700
    path.write_text(cache.serialize(), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600


def build_msal_app(settings: Settings) -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    if not settings.azure_client_id:
        raise AuthRequiredError(
            "未配置 AZURE_CLIENT_ID（Entra 应用注册的 Application ID），无法认证"
        )
    cache = _load_cache(settings)
    app = msal.PublicClientApplication(
        client_id=settings.azure_client_id,
        authority=f"https://login.microsoftonline.com/{settings.azure_tenant_id}",
        token_cache=cache,
    )
    return app, cache


def device_login(settings: Settings) -> dict:
    """设备代码登录。返回结果摘要（不含任何令牌）。"""
    app, cache = build_msal_app(settings)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise AuthRequiredError(f"无法发起设备代码登录: {flow.get('error_description', 'unknown')}")
    # 由 CLI 负责把 flow["message"] 打印给用户
    print(flow["message"], flush=True)
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise AuthRequiredError(
            f"登录失败: {result.get('error')} - {result.get('error_description')}"
        )
    _save_cache(cache, settings)
    return {"account": (result.get("id_token_claims") or {}).get("preferred_username", "unknown")}


def acquire_token_silent(settings: Settings) -> str:
    """静默获取（必要时用 refresh token 续期）访问令牌。失败抛 AuthRequiredError。"""
    app, cache = build_msal_app(settings)
    accounts = app.get_accounts()
    if not accounts:
        raise AuthRequiredError()
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache, settings)
    if not result or "access_token" not in result:
        raise AuthRequiredError()
    return result["access_token"]


def logout(settings: Settings) -> bool:
    path = _cache_path(settings)
    if path.exists():
        path.unlink()
        return True
    return False


def status(settings: Settings) -> dict:
    """登录状态摘要（不含令牌内容）。"""
    app, _ = build_msal_app(settings)
    accounts = app.get_accounts()
    if not accounts:
        return {"logged_in": False}
    acct = accounts[0]
    token_preview = None
    result = app.acquire_token_silent(SCOPES, account=acct)
    if result and "access_token" in result:
        token_preview = {
            "expires_on": result.get("expires_on") or result.get("expires_in"),
            "token_source": result.get("token_source", "unknown"),
        }
    return {
        "logged_in": True,
        "account": acct.get("username", "unknown"),
        "tenant": settings.azure_tenant_id,
        "token": token_preview,
        "cache_path": str(_cache_path(settings)),
    }


def debug_dump_claims(settings: Settings) -> dict:  # pragma: no cover - 仅排障用
    """仅返回非敏感元信息；绝不返回令牌本体。"""
    s = status(settings)
    return json.loads(json.dumps(s))
