"""任务存储与入队：默认 Redis + worker；JOB_BACKEND=memory 时回退进程内线程。"""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from lesson_prep.config import JOB_BACKEND
from lesson_prep.logutil import safe_log

Status = Literal["pending", "running", "done", "error", "awaiting_confirmation"]
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
    "awaiting_plan_confirm": "等待老师确认教案",
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

REDIS_PREFIX = "lessonprep"
QUEUE_KEY = f"{REDIS_PREFIX}:queue"
JOB_KEY = f"{REDIS_PREFIX}:job:{{job_id}}"
JOB_IDS_KEY = f"{REDIS_PREFIX}:job_ids"


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
    parallel_lanes: dict[str, Any] | None = None
    thread_id: str | None = None
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
            "parallel_lanes": self.parallel_lanes,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunJob":
        return cls(
            id=str(data["id"]),
            status=data.get("status") or "pending",  # type: ignore[arg-type]
            step=str(data.get("step") or "queued"),
            message=str(data.get("message") or ""),
            input=data.get("input") or {},
            result=data.get("result"),
            error=data.get("error"),
            failed_step=data.get("failed_step"),
            parallel_lanes=data.get("parallel_lanes"),
            thread_id=data.get("thread_id"),
            created_at=str(data.get("created_at") or datetime.now().isoformat(timespec="seconds")),
            updated_at=str(data.get("updated_at") or datetime.now().isoformat(timespec="seconds")),
        )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _task_payload(
    *,
    job_id: str,
    action: str,
    resume_from: str | None = None,
    prior_result: dict[str, Any] | None = None,
    resume_value: Any = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "action": action,
        "resume_from": resume_from,
        "prior_result": prior_result,
        "resume_value": resume_value,
    }


class MemoryJobStore:
    """进程内回退：入队后用后台线程执行（测试 / 无 Redis 时）。"""

    def __init__(self) -> None:
        self._jobs: dict[str, RunJob] = {}
        self._lock = threading.Lock()

    def create(self, lesson_input: dict[str, Any]) -> RunJob:
        job = RunJob(
            id=uuid.uuid4().hex[:12],
            input=lesson_input,
            thread_id=uuid.uuid4().hex,
            message="排队中",
        )
        with self._lock:
            self._jobs[job.id] = job
        self._spawn(job.id, action="run")
        return job

    def rerun(
        self,
        job_id: str,
        from_step: PipelineStep,
        *,
        prior_result: dict[str, Any] | None = None,
    ) -> RunJob:
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
            job.updated_at = _now()
            if prior is not None:
                job.result = prior
            # 重跑使用新 thread，避免旧 interrupt 状态干扰
            job.thread_id = f"{job_id}-{uuid.uuid4().hex[:6]}"
        self._spawn(job_id, action="rerun", resume_from=from_step, prior_result=prior_result)
        updated = self.get(job_id)
        assert updated is not None
        return updated

    def confirm_plan(
        self,
        job_id: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> RunJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.status == "running":
                raise RuntimeError("任务正在运行")
            if job.status != "awaiting_confirmation":
                raise RuntimeError("当前不在「等待确认教案」状态")
            prior = result if result is not None else job.result
            if not prior or not prior.get("lesson_plan"):
                raise ValueError("缺少教案，无法继续生成")
            prior = dict(prior)
            prior["awaiting_plan_confirm"] = False
            job.status = "pending"
            job.step = "materials"
            job.message = "教案已确认，开始并行生成"
            job.error = None
            job.failed_step = None
            job.result = prior
            job.updated_at = _now()
            resume_value = {
                "lesson_plan": prior.get("lesson_plan"),
                "teaching_anchors": prior.get("teaching_anchors"),
                "lesson_plan_qa": prior.get("lesson_plan_qa"),
            }
        self._spawn(job_id, action="confirm", prior_result=prior, resume_value=resume_value)
        updated = self.get(job_id)
        assert updated is not None
        return updated

    def get(self, job_id: str) -> RunJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in kwargs.items():
                if value is not None and hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = _now()

    def _spawn(
        self,
        job_id: str,
        *,
        action: str,
        resume_from: str | None = None,
        prior_result: dict[str, Any] | None = None,
        resume_value: Any = None,
    ) -> None:
        threading.Thread(
            target=execute_job_task,
            args=(
                self,
                _task_payload(
                    job_id=job_id,
                    action=action,
                    resume_from=resume_from,
                    prior_result=prior_result,
                    resume_value=resume_value,
                ),
            ),
            name=f"job-{action}-{job_id}",
            daemon=True,
        ).start()


class RedisJobStore:
    """Redis 持久化 Job + 队列；由独立 worker 消费。"""

    def create(self, lesson_input: dict[str, Any]) -> RunJob:
        job = RunJob(
            id=uuid.uuid4().hex[:12],
            input=lesson_input,
            thread_id=uuid.uuid4().hex,
            message="已入队，等待 worker",
        )
        self._save(job)
        self._enqueue(_task_payload(job_id=job.id, action="run"))
        return job

    def rerun(
        self,
        job_id: str,
        from_step: PipelineStep,
        *,
        prior_result: dict[str, Any] | None = None,
    ) -> RunJob:
        if from_step not in VALID_RERUN_STEPS:
            raise ValueError(f"不支持的重跑步骤: {from_step}")
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job.status == "running":
            raise RuntimeError("任务正在运行，请稍后再重跑")
        prior = prior_result if prior_result is not None else job.result
        if from_step != "curriculum" and not prior:
            raise ValueError("没有可复用的上游结果，请先完整生成或从课标重跑")

        job.status = "pending"
        job.step = from_step
        job.message = f"已入队，准备从「{STEP_LABELS.get(from_step, from_step)}」重跑"
        job.error = None
        job.failed_step = None
        job.updated_at = _now()
        if prior is not None:
            job.result = prior
        job.thread_id = f"{job_id}-{uuid.uuid4().hex[:8]}"
        self._save(job)
        self._enqueue(
            _task_payload(
                job_id=job.id,
                action="rerun",
                resume_from=from_step,
                prior_result=prior,
            )
        )
        return job

    def confirm_plan(
        self,
        job_id: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> RunJob:
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job.status == "running":
            raise RuntimeError("任务正在运行")
        if job.status != "awaiting_confirmation":
            raise RuntimeError("当前不在「等待确认教案」状态")

        prior = result if result is not None else job.result
        if not prior or not prior.get("lesson_plan"):
            raise ValueError("缺少教案，无法继续生成")
        prior = dict(prior)
        prior["awaiting_plan_confirm"] = False
        # 优先用结果里的 thread_id，保证 resume 同一 LangGraph 线程
        if prior.get("thread_id"):
            job.thread_id = str(prior["thread_id"])
        elif not job.thread_id:
            job.thread_id = job.id

        job.status = "pending"
        job.step = "materials"
        job.message = "教案已确认，已入队继续生成"
        job.error = None
        job.failed_step = None
        job.result = prior
        job.updated_at = _now()
        self._save(job)

        resume_value = {
            "lesson_plan": prior.get("lesson_plan"),
            "teaching_anchors": prior.get("teaching_anchors"),
            "lesson_plan_qa": prior.get("lesson_plan_qa"),
        }
        self._enqueue(
            _task_payload(
                job_id=job.id,
                action="confirm",
                prior_result=prior,
                resume_value=resume_value,
            )
        )
        return job

    def get(self, job_id: str) -> RunJob | None:
        from lesson_prep.redis_client import get_redis

        raw = get_redis().get(JOB_KEY.format(job_id=job_id))
        if not raw:
            return None
        return RunJob.from_dict(json.loads(raw))

    def update(self, job_id: str, **kwargs: Any) -> None:
        job = self.get(job_id)
        if not job:
            return
        for key, value in kwargs.items():
            if value is not None and hasattr(job, key):
                setattr(job, key, value)
        job.updated_at = _now()
        self._save(job)

    def _save(self, job: RunJob) -> None:
        from lesson_prep.redis_client import get_redis

        r = get_redis()
        r.set(JOB_KEY.format(job_id=job.id), json.dumps(job.to_dict(), ensure_ascii=False))
        r.zadd(JOB_IDS_KEY, {job.id: datetime.now().timestamp()})

    def _enqueue(self, task: dict[str, Any]) -> None:
        from lesson_prep.redis_client import get_redis

        get_redis().rpush(QUEUE_KEY, json.dumps(task, ensure_ascii=False))
        safe_log(f"[queue] enqueued job={task.get('job_id')} action={task.get('action')}")


def execute_job_task(store: Any, task: dict[str, Any]) -> None:
    """执行单次任务（worker / memory 线程共用）。"""
    from lesson_prep.graph import run_preparation

    job_id = str(task["job_id"])
    action = str(task.get("action") or "run")
    resume_from = task.get("resume_from")
    prior_override = task.get("prior_result")
    resume_value = task.get("resume_value")

    job = store.get(job_id)
    if not job:
        safe_log(f"[worker] job 不存在: {job_id}")
        return

    start_step = resume_from or ("materials" if action == "confirm" else "curriculum")
    store.update(
        job_id,
        status="running",
        step=start_step,
        message=STEP_LABELS.get(str(start_step), str(start_step)) + "工作中",
        error="",
        failed_step="",
        parallel_lanes={},
    )

    thread_id = job.thread_id or job_id
    if action != "confirm" and not job.thread_id:
        thread_id = uuid.uuid4().hex
        store.update(job_id, thread_id=thread_id)

    def on_progress(step: str, message: str, detail: dict | None = None) -> None:
        kwargs: dict[str, Any] = {"status": "running", "step": step, "message": message}
        if detail and isinstance(detail.get("lanes"), dict):
            kwargs["parallel_lanes"] = detail["lanes"]
        store.update(job_id, **kwargs)

    def on_checkpoint(partial: dict) -> None:
        lanes = partial.get("materials_lanes") if isinstance(partial, dict) else None
        if isinstance(lanes, dict):
            store.update(job_id, result=partial, parallel_lanes=lanes, thread_id=partial.get("thread_id") or thread_id)
        else:
            store.update(job_id, result=partial, thread_id=partial.get("thread_id") or thread_id)

    try:
        prior = prior_override if prior_override is not None else job.result
        if action == "confirm":
            result = run_preparation(
                job.input,
                on_progress=on_progress,
                on_checkpoint=on_checkpoint,
                thread_id=thread_id,
                resume_value=resume_value
                or {
                    "lesson_plan": (prior or {}).get("lesson_plan"),
                    "teaching_anchors": (prior or {}).get("teaching_anchors"),
                    "lesson_plan_qa": (prior or {}).get("lesson_plan_qa"),
                },
                pause_after_plan=False,
            )
        else:
            result = run_preparation(
                job.input,
                on_progress=on_progress,
                on_checkpoint=on_checkpoint,
                resume_from=resume_from,  # type: ignore[arg-type]
                prior_state=prior if resume_from else (prior if action == "rerun" else None),
                thread_id=thread_id,
                pause_after_plan=None,
            )

        tid = result.get("thread_id") or thread_id
        lanes = result.get("materials_lanes") if isinstance(result, dict) else None
        awaiting = bool(result.get("awaiting_plan_confirm"))
        if awaiting:
            store.update(
                job_id,
                status="awaiting_confirmation",
                step="awaiting_plan_confirm",
                message="教案已就绪，请确认或修改后继续",
                result=result,
                error="",
                failed_step="",
                parallel_lanes={},
                thread_id=tid,
            )
        else:
            store.update(
                job_id,
                status="done",
                step="done",
                message="备课完成",
                result=result,
                error="",
                failed_step="",
                parallel_lanes=lanes if isinstance(lanes, dict) else {},
                thread_id=tid,
            )
    except Exception as exc:  # noqa: BLE001
        job2 = store.get(job_id)
        partial = job2.result if job2 else None
        store.update(
            job_id,
            status="error",
            message="生成失败（可从失败节点重跑）",
            error=f"{exc}\n{traceback.format_exc()}",
            result=partial,
            failed_step=resume_from or start_step,
        )


def create_job_store():
    backend = JOB_BACKEND
    if backend == "memory":
        safe_log("[jobs] backend=memory")
        return MemoryJobStore()
    try:
        from lesson_prep.redis_client import get_redis

        get_redis()
        safe_log("[jobs] backend=redis")
        return RedisJobStore()
    except Exception as exc:  # noqa: BLE001
        safe_log(f"[jobs] redis 不可用，回退 memory: {exc}")
        return MemoryJobStore()


job_store = create_job_store()
