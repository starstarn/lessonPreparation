from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lesson_prep.catalog import load_catalog  # noqa: E402
from lesson_prep.config import MOCK_LLM, OPENAI_MODEL  # noqa: E402
from lesson_prep.export_docs import (  # noqa: E402
    build_export_basename,
    export_docx,
    export_pdf,
    export_pptx,
)
from lesson_prep.jobs import job_store  # noqa: E402
from lesson_prep.schemas import LessonInput  # noqa: E402
from lesson_prep.versions import (  # noqa: E402
    delete_version,
    get_version,
    list_versions,
    save_version,
)

app = FastAPI(title="智能备课教研团队 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResultUpdate(BaseModel):
    result: dict[str, Any]


class VersionCreate(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any]
    note: str = ""
    title: str | None = None


class ExportRequest(BaseModel):
    format: Literal["docx", "pdf", "pptx"]
    module: Literal["all", "curriculum", "plan", "exercises", "slides", "board"] = "all"
    input: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any]


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mock_llm": MOCK_LLM,
        "model": OPENAI_MODEL,
    }


@app.get("/api/catalog")
def get_catalog():
    """年级 → 学习领域 → 主题/单元 → 课时 级联目录。"""
    return load_catalog()


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


@app.put("/api/runs/{run_id}/result")
def update_run_result(run_id: str, payload: ResultUpdate):
    job = job_store.get(run_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    job_store.update(run_id, result=payload.result, message="已保存编辑")
    updated = job_store.get(run_id)
    return updated.to_dict() if updated else {"id": run_id, "result": payload.result}


@app.get("/api/versions")
def versions_list():
    return {"items": list_versions()}


@app.post("/api/versions")
def versions_create(payload: VersionCreate):
    return save_version(
        lesson_input=payload.input,
        result=payload.result,
        note=payload.note,
        title=payload.title,
    )


@app.get("/api/versions/{version_id}")
def versions_get(version_id: str):
    data = get_version(version_id)
    if not data:
        raise HTTPException(status_code=404, detail="版本不存在")
    return data


@app.delete("/api/versions/{version_id}")
def versions_delete(version_id: str):
    if not delete_version(version_id):
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"ok": True}


@app.post("/api/export")
def export_prep(payload: ExportRequest):
    fmt = payload.format
    module = payload.module
    if fmt == "pptx" and module not in {"all", "slides", "plan", "curriculum", "board"}:
        raise HTTPException(status_code=400, detail="不支持的导出模块")
    try:
        if fmt == "docx":
            content = export_docx(payload.input, payload.result, module=module)
            media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif fmt == "pdf":
            content = export_pdf(payload.input, payload.result, module=module)
            media = "application/pdf"
        else:
            content = export_pptx(payload.input, payload.result, module=module)
            media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"导出失败：{exc}") from exc

    filename = f"{build_export_basename(payload.input, module)}.{fmt}"
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or f"lesson.{fmt}"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{ascii_name}"'},
    )
