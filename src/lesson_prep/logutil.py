from __future__ import annotations

import logging
import sys

logger = logging.getLogger("lesson_prep")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def safe_log(message: str) -> None:
    """Windows + uvicorn 线程下 print(flush=True) 可能触发 Errno 22，改用 logging。"""
    try:
        logger.info(message)
    except OSError:
        pass
