from __future__ import annotations

import threading
from collections.abc import Callable

ProgressCb = Callable[[str, str], None]

_local = threading.local()


def set_progress_callback(cb: ProgressCb | None) -> None:
    _local.callback = cb


def report_progress(step: str, message: str) -> None:
    cb = getattr(_local, "callback", None)
    if cb:
        cb(step, message)
