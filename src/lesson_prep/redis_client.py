"""Redis 连接（任务队列 / Job 状态）。"""

from __future__ import annotations

import threading
from typing import Any

from lesson_prep.config import REDIS_URL
from lesson_prep.logutil import safe_log

_lock = threading.Lock()
_client: Any | None = None


def get_redis():
    """返回同步 Redis 客户端；连接失败时抛错。"""
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is not None:
            return _client
        import redis

        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        _client = client
        safe_log(f"[redis] connected {REDIS_URL}")
        return _client


def redis_ping() -> bool:
    try:
        get_redis().ping()
        return True
    except Exception:  # noqa: BLE001
        return False
