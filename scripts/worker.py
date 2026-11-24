#!/usr/bin/env python3
"""Redis 队列 worker：消费 lessonprep:queue，执行 LangGraph 备课任务。"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ensure_venv() -> None:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if os.name == "nt":
        venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        print(f"未找到虚拟环境: {venv_python}", file=sys.stderr)
        raise SystemExit(1)
    in_venv = Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()
    if in_venv:
        return
    print(f"改用项目虚拟环境: {venv_python}", flush=True)
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_venv()
sys.path.insert(0, str(ROOT / "src"))

from lesson_prep.config import JOB_BACKEND, REDIS_URL  # noqa: E402
from lesson_prep.jobs import QUEUE_KEY, RedisJobStore, execute_job_task  # noqa: E402
from lesson_prep.logutil import safe_log  # noqa: E402
from lesson_prep.redis_client import get_redis  # noqa: E402

_running = True


def _stop(*_args) -> None:
    global _running
    _running = False
    safe_log("[worker] shutting down...")


def main() -> int:
    if JOB_BACKEND != "redis":
        print(
            f"JOB_BACKEND={JOB_BACKEND}，worker 仅用于 redis 后端。"
            "请设置 JOB_BACKEND=redis 与 REDIS_URL。",
            file=sys.stderr,
        )
        return 2

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    r = get_redis()
    store = RedisJobStore()
    safe_log(f"[worker] listening on {QUEUE_KEY} redis={REDIS_URL}")

    while _running:
        item = r.blpop(QUEUE_KEY, timeout=2)
        if not item:
            continue
        _, raw = item
        try:
            task = json.loads(raw)
        except json.JSONDecodeError:
            safe_log(f"[worker] 无效任务 JSON: {raw!r}")
            continue
        job_id = task.get("job_id")
        safe_log(f"[worker] start job={job_id} action={task.get('action')}")
        try:
            execute_job_task(store, task)
            safe_log(f"[worker] done job={job_id}")
        except Exception as exc:  # noqa: BLE001
            safe_log(f"[worker] job={job_id} 未捕获异常: {exc}")
            time.sleep(0.5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
