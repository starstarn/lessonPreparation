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
PipelineStep = Literal[
    "curriculum",
    "lesson_plan",
    "materials",
    "consistency",
    "exercises",
    "slides",
    "blackboard",
]

STEP_LABELS = {
    "queued": "排队中",
    "curriculum": "课标解读员",
    "lesson_plan": "教案设计师",
    "lesson_plan_review": "教案审核员",
    "materials": "并行生成（课件/习题/板书）",
    "consistency": "一致性检查员",
    "exercises": "习题组卷师",
    "slides": "课件生成师",
    "blackboard": "板书设计师",
    "done": "已完成",
}

VALID_RERUN_STEPS: set[str] = {
    "curriculum",
    "lesson_plan",
    "materials",
    "consistency",
    "exercises",
    "slides",
    "blackboard",
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
    failed_step: str | None = None
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
            "failed_step": self.failed_step,
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
            args=(job.id, None, None),
            name=f"job-{job.id}",
            daemon=True,
        ).start()
        return job

    def rerun(
        self,
        job_id: str,
        from_step: PipelineStep,
        *,
        prior_result: dict[str, Any] | None = None,
    ) -> RunJob:
        """从指定节点重跑（保留上游产物）。"""
        if from_step not in VALID_RERUN_STEPS:
            raise ValueError(f"不支持的重跑步骤: {from_step}")

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.status == "running":
                raise RuntimeError("任务正在运行，请稍后再重跑")

            prior = prior_result if prior_result is not None else job.result
            if from_step != "curriculum" and not prior:
                raise ValueError("没有可复用的上游结果，请先完整生成或从课标重跑")

            job.status = "pending"
            job.step = from_step
            job.message = f"准备从「{STEP_LABELS.get(from_step, from_step)}」重跑"
            job.error = None
            job.failed_step = None
            job.updated_at = datetime.now().isoformat(timespec="seconds")
            # 先保留 prior，成功后再覆盖；失败时 checkpoint 会带回部分结果
            if prior is not None:
                job.result = prior

        threading.Thread(
            target=self._execute,
            args=(job_id, from_step, prior_result),
            name=f"job-rerun-{job_id}",
            daemon=True,
        ).start()
        updated = self.get(job_id)
        assert updated is not None
        return updated

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
        failed_step: str | None = None,
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
            if failed_step is not None:
                job.failed_step = failed_step
            job.updated_at = datetime.now().isoformat(timespec="seconds")

    def _execute(
        self,
        job_id: str,
        resume_from: str | None,
        prior_override: dict[str, Any] | None,
    ) -> None:
        job = self.get(job_id)
        if not job:
            return

        start_step = resume_from or "curriculum"
        self.update(
            job_id,
            status="running",
            step=start_step,
            message=STEP_LABELS.get(start_step, start_step) + "工作中",
            error="",
            failed_step="",
        )

        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        input_path = job_dir / "input.json"
        output_path = job_dir / "output.json"
        progress_path = job_dir / "output.progress.json"
        checkpoint_path = job_dir / "output.checkpoint.json"
        prior_path = job_dir / "prior.json"

        input_path.write_text(json.dumps(job.input, ensure_ascii=False), encoding="utf-8")
        for p in (output_path, progress_path, checkpoint_path):
            if p.exists():
                p.unlink()

        prior = prior_override if prior_override is not None else job.result
        cmd = [PYTHON, str(RUN_JOB_SCRIPT), str(input_path), str(output_path)]
        if resume_from:
            if prior is None and resume_from != "curriculum":
                self.update(
                    job_id,
                    status="error",
                    message="重跑失败",
                    error="缺少上游结果，无法从该步骤重跑",
                    failed_step=resume_from,
                )
                return
            prior_path.write_text(
                json.dumps(prior or {}, ensure_ascii=False),
                encoding="utf-8",
            )
            cmd.extend([resume_from, str(prior_path)])

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        stdout_path = job_dir / "stdout.log"
        stderr_path = job_dir / "stderr.log"

        try:
            with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open(
                "w", encoding="utf-8"
            ) as err:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    env=env,
                    stdout=out,
                    stderr=err,
                )

                while proc.poll() is None:
                    self._read_progress(job_id, progress_path)
                    self._read_checkpoint(job_id, checkpoint_path)
                    time.sleep(0.5)

                self._read_progress(job_id, progress_path)
                self._read_checkpoint(job_id, checkpoint_path)

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
                    error="",
                    failed_step="",
                )
            else:
                partial = data.get("partial_result")
                if partial is None and checkpoint_path.exists():
                    try:
                        partial = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        partial = None
                failed = self._infer_failed_step(progress_path, resume_from)
                self.update(
                    job_id,
                    status="error",
                    message="生成失败（可从失败节点重跑）",
                    error=data.get("error") or "未知错误",
                    result=partial if isinstance(partial, dict) else job.result,
                    failed_step=failed,
                )
        except Exception as exc:  # noqa: BLE001
            partial = None
            if checkpoint_path.exists():
                try:
                    partial = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    partial = None
            failed = self._infer_failed_step(progress_path, resume_from)
            self.update(
                job_id,
                status="error",
                message="生成失败（可从失败节点重跑）",
                error=f"{exc}\n{traceback.format_exc()}",
                result=partial if isinstance(partial, dict) else None,
                failed_step=failed,
            )

    def _infer_failed_step(self, progress_path: Path, resume_from: str | None) -> str:
        if progress_path.exists():
            try:
                data = json.loads(progress_path.read_text(encoding="utf-8"))
                step = data.get("step")
                if step in VALID_RERUN_STEPS:
                    return str(step)
            except Exception:  # noqa: BLE001
                pass
        return resume_from or "curriculum"

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

    def _read_checkpoint(self, job_id: str, checkpoint_path: Path) -> None:
        """运行中同步部分结果，便于失败后展示已完成节点。"""
        if not checkpoint_path.exists():
            return
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.update(job_id, result=data)
        except Exception:  # noqa: BLE001
            pass


job_store = JobStore()
