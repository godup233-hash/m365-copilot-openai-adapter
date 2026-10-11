"""分级日志 + 敏感信息脱敏。

脱敏目标：Access/Refresh Token、Bearer 头、Adapter API Key 永远不进日志。
聊天内容不进 INFO 及以上级别（仅在 DEBUG 且显式开启时由调用方自行决定，
本项目的所有日志调用均不输出消息正文）。
"""
from __future__ import annotations

import logging
import re
import sys

_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"(eyJ[A-Za-z0-9\-._~+/]{20,})"),  # JWT 形态
    re.compile(r"([?&](?:api_key|apikey|key|access_token)=)[^\s&]+", re.IGNORECASE),
]


class RedactFilter(logging.Filter):
    def __init__(self, extra_secrets: list[str] | None = None) -> None:
        super().__init__()
        self._literal_secrets = [s for s in (extra_secrets or []) if s and len(s) >= 8]

    def _redact(self, text: str) -> str:
        for pat in _PATTERNS:
            if pat.pattern.startswith("([?&]"):
                text = pat.sub(r"\1***", text)
            else:
                text = pat.sub(lambda m: ("Bearer ***" if m.group(0).lower().startswith("bearer") else "***"), text)
        for secret in self._literal_secrets:
            text = text.replace(secret, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self._redact(record.getMessage())
            record.args = ()
        except Exception:  # 脱敏失败也不阻塞日志
            pass
        return True


def setup_logging(level: str = "INFO", extra_secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    handler.addFilter(RedactFilter(extra_secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # 压低第三方库噪音，且 msal 的日志可能含敏感信息，强制不高于 WARNING
    logging.getLogger("msal").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
