"""会话映射存储：session_key → Graph conversation_id。

- 内存存储 + TTL 惰性清理（单进程部署够用；多副本部署需换共享存储，README 已说明）
- 每个会话一把 asyncio.Lock，同会话请求串行化（Copilot 会话有状态，并发必串味）
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class SessionEntry:
    session_key: str
    conversation_id: str
    created_at: float
    last_used: float
    turn_count: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionStore:
    def __init__(self, ttl_seconds: int = 86400) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, SessionEntry] = {}

    def get(self, session_key: str) -> SessionEntry | None:
        entry = self._entries.get(session_key)
        if entry is None:
            return None
        if time.time() - entry.last_used > self._ttl:
            del self._entries[session_key]
            return None
        return entry

    def set(self, session_key: str, conversation_id: str) -> SessionEntry:
        now = time.time()
        entry = SessionEntry(
            session_key=session_key,
            conversation_id=conversation_id,
            created_at=now,
            last_used=now,
        )
        self._entries[session_key] = entry
        return entry

    def touch(self, entry: SessionEntry) -> None:
        entry.last_used = time.time()

    def invalidate(self, session_key: str) -> None:
        self._entries.pop(session_key, None)

    def cleanup(self) -> int:
        now = time.time()
        expired = [k for k, e in self._entries.items() if now - e.last_used > self._ttl]
        for k in expired:
            del self._entries[k]
        return len(expired)

    def __len__(self) -> int:
        return len(self._entries)
