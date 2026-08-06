from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

Status = Literal["pending", "running", "done", "error"]

STEP_LABELS = {
    "queued": "排队中",
    "curriculum": "课标解读员",
    "lesson_plan": "教案设计师",
    "exercises": "习题组卷师",
    "slides": "课件生成师",
    "blackboard": "板书设计师",
    "done": "已完成",
}

ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = ROOT / "logs" / "jobs"
RUN_JOB_SCRIPT = ROOT / "scripts" / "run_job.py"
PYTHON = sys.executable


@dataclass
class RunJob:
    id: str
    status: Status = "pending"
    step: str = "queued"
    message: str = "等待开始"
    input: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "step": self.step,
            "step_label": STEP_LABELS.get(self.step, self.step),
            "message": self.message,
            "input": self.input,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobStore:
    """用独立子进程跑备课流水线，彻底避开 Windows 线程里 SSL/stdout 的 Errno 22。"""

    def __init__(self) -> None:
        self._jobs: dict[str, RunJob] = {}
        self._lock = threading.Lock()
        JOBS_DIR.mkdir(parents=True, exist_ok=True)

    def create(self, lesson_input: dict[str, Any]) -> RunJob:
        job = RunJob(id=uuid.uuid4().hex[:12], input=lesson_input)
        with self._lock:
            self._jobs[job.id] = job
        threading.Thread(
            target=self._execute,
            args=(job.id,),
            name=f"job-{job.id}",
            daemon=True,
        ).start()
        return job

    def get(self, job_id: str) -> RunJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(
        self,
        job_id: str,
        *,
        status: Status | None = None,
        step: str | None = None,
        message: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if status is not None:
                job.status = status
            if step is not None:
                job.step = step
            if message is not None:
                job.message = message
            if result is not None:
                job.result = result
            if error is not None:
                job.error = error
            job.updated_at = datetime.now().isoformat(timespec="seconds")

    def _execute(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job:
            return

        self.update(job_id, status="running", step="curriculum", message="课标解读员工作中")

        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        input_path = job_dir / "input.json"
        output_path = job_dir / "output.json"
        progress_path = job_dir / "output.progress.json"

        input_path.write_text(json.dumps(job.input, ensure_ascii=False), encoding="utf-8")
        if output_path.exists():
            output_path.unlink()
        if progress_path.exists():
            progress_path.unlink()

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        # 子进程日志落到文件，避免控制台写坏
        stdout_path = job_dir / "stdout.log"
        stderr_path = job_dir / "stderr.log"

        try:
            with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open(
                "w", encoding="utf-8"
            ) as err:
                proc = subprocess.Popen(
                    [PYTHON, str(RUN_JOB_SCRIPT), str(input_path), str(output_path)],
                    cwd=str(ROOT),
                    env=env,
                    stdout=out,
                    stderr=err,
                )

                while proc.poll() is None:
                    self._read_progress(job_id, progress_path)
                    time.sleep(0.5)

                self._read_progress(job_id, progress_path)

            if not output_path.exists():
                err_text = stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise RuntimeError(f"子进程未写出结果 (exit={proc.returncode})\n{err_text}")

            data = json.loads(output_path.read_text(encoding="utf-8"))
            if data.get("ok"):
                self.update(
                    job_id,
                    status="done",
                    step="done",
                    message="备课完成",
                    result=data["result"],
                )
            else:
                self.update(
                    job_id,
                    status="error",
                    message="生成失败",
                    error=data.get("error") or "未知错误",
                )
        except Exception as exc:  # noqa: BLE001
            self.update(
                job_id,
                status="error",
                message="生成失败",
                error=f"{exc}\n{traceback.format_exc()}",
            )

    def _read_progress(self, job_id: str, progress_path: Path) -> None:
        if not progress_path.exists():
            return
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            step = data.get("step")
            message = data.get("message")
            if step or message:
                self.update(
                    job_id,
                    status="running",
                    step=step,
                    message=message,
                )
        except Exception:  # noqa: BLE001
            pass


job_store = JobStore()
