from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lesson_prep.config import MOCK_LLM, OPENAI_MODEL  # noqa: E402
from lesson_prep.jobs import job_store  # noqa: E402
from lesson_prep.schemas import LessonInput  # noqa: E402

app = FastAPI(title="智能备课教研团队 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mock_llm": MOCK_LLM,
        "model": OPENAI_MODEL,
    }


@app.post("/api/runs")
def create_run(payload: LessonInput):
    job = job_store.create(payload.model_dump())
    return job.to_dict()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    job = job_store.get(run_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job.to_dict()
