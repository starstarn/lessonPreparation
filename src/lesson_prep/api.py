from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
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
from lesson_prep.media_assets import resolve_media_path  # noqa: E402
from lesson_prep.plugins import plugins_public, profiles_public  # noqa: E402
from lesson_prep.schemas import LessonInput  # noqa: E402
from lesson_prep.skills import skills_public  # noqa: E402
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


class RerunRequest(BaseModel):
    from_step: Literal[
        "curriculum",
        "lesson_plan",
        "materials",
        "consistency",
        "exercises",
        "slides",
        "blackboard",
    ]
    result: dict[str, Any] | None = None


class ConfirmPlanRequest(BaseModel):
    """确认教案并继续并行生成；可附带老师编辑后的完整 result。"""

    result: dict[str, Any] | None = None


@app.get("/api/health")
def health():
    from lesson_prep.config import (
        LLM_FALLBACK_ENABLED,
        LLM_MODEL_FALLBACK,
        LLM_MODEL_FAST,
        LLM_MODEL_STRONG,
        LLM_ROUTING_ENABLED,
        PLAN_CONFIRM_GATE,
    )

    return {
        "ok": True,
        "mock_llm": MOCK_LLM,
        "model": OPENAI_MODEL,
        "plan_confirm_gate": PLAN_CONFIRM_GATE,
        "llm_routing": {
            "enabled": LLM_ROUTING_ENABLED,
            "fallback_enabled": LLM_FALLBACK_ENABLED,
            "fast": LLM_MODEL_FAST,
            "strong": LLM_MODEL_STRONG,
            "fallback": LLM_MODEL_FALLBACK or None,
        },
    }


@app.get("/api/catalog")
def get_catalog():
    """年级 → 学习领域 → 主题/单元 → 课时 级联目录。"""
    return load_catalog()


@app.get("/api/agent-plugins")
def get_agent_plugins():
    """可插拔 Agent 注册表（能力层）。"""
    return {"plugins": plugins_public()}


@app.get("/api/agent-skills")
def get_agent_skills():
    """可复用 Skill 能力包（比 Agent 更细）。"""
    return {"skills": skills_public()}


@app.get("/api/agent-profiles")
def get_agent_profiles():
    """场景装配模板（编排层）。"""
    return {"profiles": profiles_public()}


@app.get("/api/media/{filename}")
def get_media(filename: str):
    """课件配图（search_images / generate_diagram 产出）。"""
    import mimetypes

    path = resolve_media_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


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


@app.post("/api/runs/{run_id}/rerun")
def rerun_run(run_id: str, payload: RerunRequest):
    """从指定节点重跑；可附带当前编辑后的 result 作为上游状态。"""
    try:
        job = job_store.rerun(run_id, payload.from_step, prior_result=payload.result)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job.to_dict()


@app.post("/api/runs/{run_id}/confirm-plan")
def confirm_plan(run_id: str, payload: ConfirmPlanRequest | None = None):
    """老师确认教案后继续并行生成课件/习题/板书。"""
    body = payload or ConfirmPlanRequest()
    try:
        job = job_store.confirm_plan(run_id, result=body.result)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
