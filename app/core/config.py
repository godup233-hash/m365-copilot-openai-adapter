"""全局配置：全部走环境变量 / .env，不硬编码任何凭据。"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 必填
    adapter_api_key: str = ""
    azure_client_id: str = ""

    # 可选
    azure_tenant_id: str = "organizations"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    graph_base_url: str = "https://graph.microsoft.com/beta"
    token_cache_path: str = "~/.config/m365-adapter/token_cache.bin"
    model_alias: str = "m365-copilot-chat"
    default_timezone: str = "Asia/Shanghai"
    session_ttl_seconds: int = 86400
    session_recovery: str = "off"  # off | auto_new
    inject_history_on_first_turn: bool = True
    request_timeout_seconds: float = 120.0
    stream_timeout_seconds: float = 300.0
    upstream_max_retries: int = 3
    upstream_concurrency: int = 4
    max_request_bytes: int = 1_048_576
    retry_base_seconds: float = 1.0  # 指数退避基数；测试可调小


@lru_cache
def get_settings() -> Settings:
    return Settings()
