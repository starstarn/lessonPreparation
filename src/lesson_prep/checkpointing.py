"""LangGraph checkpointer：默认 SQLite（持久化 HITL），测试可用 Memory。"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from lesson_prep.config import CHECKPOINT_SQLITE_PATH, LANGGRAPH_CHECKPOINT
from lesson_prep.logutil import safe_log

_lock = threading.Lock()
_checkpointer: Any | None = None
_sqlite_conn: sqlite3.Connection | None = None


def get_checkpointer():
    """进程内单例 checkpointer（worker / API 侧共用同一文件）。"""
    global _checkpointer, _sqlite_conn
    if _checkpointer is not None:
        return _checkpointer

    with _lock:
        if _checkpointer is not None:
            return _checkpointer

        mode = LANGGRAPH_CHECKPOINT
        if mode == "memory":
            from langgraph.checkpoint.memory import MemorySaver

            _checkpointer = MemorySaver()
            safe_log("[checkpoint] 使用 MemorySaver")
            return _checkpointer

        # 默认 sqlite
        from langgraph.checkpoint.sqlite import SqliteSaver

        CHECKPOINT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_conn = sqlite3.connect(
            str(CHECKPOINT_SQLITE_PATH),
            check_same_thread=False,
        )
        saver = SqliteSaver(_sqlite_conn)
        try:
            saver.setup()
        except Exception:  # noqa: BLE001
            pass
        _checkpointer = saver
        safe_log(f"[checkpoint] 使用 SqliteSaver @ {CHECKPOINT_SQLITE_PATH}")
        return _checkpointer
