from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Literal

from lesson_prep.agents import (
    revise_exercise_paper,
    revise_lesson_plan,
    revise_blackboard,
    revise_slides,
    review_consistency,
    review_lesson_plan,
    run_blackboard_agent,
    run_curriculum_agent,
    run_exercise_agent,
    run_lesson_plan_agent,
    run_slides_agent,
    extract_teaching_anchors,
)
from lesson_prep.config import MOCK_LLM, PLAN_CONFIRM_GATE
from lesson_prep.logutil import safe_log
from lesson_prep.plugins import (
    RunPlan,
    build_run_plan,
    lane_labels,
    material_plugin_ids,
)
from lesson_prep.progress import (
    clear_materials_lanes,
    get_materials_lanes,
    report_progress,
    reset_materials_lanes,
    seed_materials_lanes,
    set_lane_status,
    set_progress_callback,
)
from lesson_prep.schemas import (
    Blackboard,
    ConsistencyReport,
    CurriculumAnalysis,
    ExercisePaper,
    LessonInput,
    LessonPlan,
    LessonPlanQAReport,
    Slides,
    TeachingAnchor,
)
from lesson_prep.state import PrepState

# 重跑入口：materials=三者并行；也可单独重跑某一设计师
PipelineStep = Literal[
    "curriculum",
    "lesson_plan",
    "materials",
    "consistency",
    "exercises",
    "slides",
    "blackboard",
]

# 主流程顺序（不含单独重跑别名）
MAIN_PIPELINE: list[str] = [
    "curriculum",
    "lesson_plan",
    "materials",
    "consistency",
]

MAX_LESSON_PLAN_REVISES = 1
MAX_CONSISTENCY_REVISES = 1

_STEP_OUTPUT_KEYS: dict[str, list[str]] = {
    "curriculum": [
        "curriculum_analysis",
        "retrieved_context",
        "lesson_plan",
        "lesson_plan_qa",
        "lesson_plan_revise_count",
        "teaching_anchors",
        "exercise_paper",
        "exercise_qa",
        "slides",
        "slides_qa",
        "blackboard",
        "consistency_qa",
        "consistency_revise_count",
    ],
    "lesson_plan": [
        "lesson_plan",
        "lesson_plan_qa",
        "lesson_plan_revise_count",
        "teaching_anchors",
        "exercise_paper",
        "exercise_qa",
        "slides",
        "slides_qa",
        "blackboard",
        "consistency_qa",
        "consistency_revise_count",
    ],
    "materials": [
        "exercise_paper",
        "exercise_qa",
        "slides",
        "slides_qa",
        "blackboard",
        "consistency_qa",
        "consistency_revise_count",
    ],
    "exercises": ["exercise_paper", "exercise_qa", "consistency_qa", "consistency_revise_count"],
    "slides": ["slides", "slides_qa", "consistency_qa", "consistency_revise_count"],
    "blackboard": ["blackboard", "consistency_qa", "consistency_revise_count"],
    "consistency": ["consistency_qa", "consistency_revise_count"],
}


def _gap() -> None:
    if not MOCK_LLM:
        time.sleep(2)


def _run_plan_from_input(lesson_input: dict[str, Any] | LessonInput) -> RunPlan:
    if isinstance(lesson_input, LessonInput):
        data = lesson_input.model_dump()
    else:
        data = lesson_input or {}
    return build_run_plan(
        profile_id=str(data.get("agent_profile") or "full"),
        enabled_agents=data.get("enabled_agents"),
    )


def _materials_label(targets: set[str] | list[str]) -> str:
    labels = lane_labels()
    return "、".join(labels.get(t, t) for t in sorted(targets))


def _commit_teaching_anchors(state: PrepState, *, announce: bool = False) -> list[TeachingAnchor]:
    """从当前教案提取三元组并写入共享状态。材料生成前会再刷一次，以吃进老师改稿。"""
    if not state.get("lesson_plan"):
        return []
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    anchors = extract_teaching_anchors(plan, lesson)
    state["teaching_anchors"] = [item.model_dump() for item in anchors]
    brief = "；".join(f"{item.knowledge_point}/{item.difficulty}" for item in anchors)
    safe_log(f"[三元组] 写入共享状态 {len(anchors)} 条：{brief}")
    if announce:
        report_progress(
            "lesson_plan",
            f"已提取教学三元组 {len(anchors)} 条，课件 / 习题 / 板书须先遵守",
        )
    return anchors


def _anchors_from_state(state: PrepState) -> list[TeachingAnchor]:
    raw = state.get("teaching_anchors") or []
    if raw:
        return [TeachingAnchor.model_validate(item) for item in raw]
    return _commit_teaching_anchors(state)


def _curriculum_node(state: PrepState) -> dict:
    report_progress("curriculum", "课标解读员工作中（按需检索课标）")
    safe_log("[课标] 课标解读员 工作中（Tool: search_curriculum）...")
    lesson = LessonInput.model_validate(state["input"])
    analysis, context = run_curriculum_agent(lesson)
    safe_log("[课标] 课标解读完成")
    _gap()
    return {
        "retrieved_context": context,
        "curriculum_analysis": analysis.model_dump(),
    }


def _lesson_plan_design_node(state: PrepState) -> dict:
    report_progress("lesson_plan", "教案设计师撰写教案")
    safe_log("[教案设计师] 撰写初稿...")
    lesson = LessonInput.model_validate(state["input"])
    raw_curriculum = state.get("curriculum_analysis")
    if raw_curriculum:
        curriculum = CurriculumAnalysis.model_validate(raw_curriculum)
    else:
        # 自定义场景跳过课标时，用空分析兜底，仍可写教案
        curriculum = CurriculumAnalysis(
            core_competencies=["（未跑课标解读，由教案设计师自行把握）"],
            academic_requirements=[],
            content_points=[lesson.lesson_title],
            teaching_tips_from_standard=[],
            confidence="low",
        )
    plan = run_lesson_plan_agent(lesson, curriculum)
    safe_log("[教案设计师] 初稿完成 → 送交教案审核员")
    _gap()
    return {
        "lesson_plan": plan.model_dump(),
        "lesson_plan_revise_count": 0,
    }


def _lesson_plan_revise_node(state: PrepState) -> dict:
    report_progress("lesson_plan", "教案设计师按审核意见修改中")
    safe_log("[教案设计师] 收到审核打回，修改中...")
    lesson = LessonInput.model_validate(state["input"])
    raw_curriculum = state.get("curriculum_analysis")
    if raw_curriculum:
        curriculum = CurriculumAnalysis.model_validate(raw_curriculum)
    else:
        curriculum = CurriculumAnalysis(confidence="low")
    plan = LessonPlan.model_validate(state["lesson_plan"])
    qa = LessonPlanQAReport.model_validate(state["lesson_plan_qa"])
    plan = revise_lesson_plan(lesson, plan, curriculum, qa)
    count = int(state.get("lesson_plan_revise_count") or 0) + 1
    safe_log(f"[教案设计师] 修改完成（第 {count} 次）→ 再次送审")
    _gap()
    return {
        "lesson_plan": plan.model_dump(),
        "lesson_plan_revise_count": count,
    }


def _lesson_plan_review_node(state: PrepState) -> dict:
    report_progress("lesson_plan_review", "教案审核员审核中")
    safe_log("[教案审核员] 审核中...")
    lesson = LessonInput.model_validate(state["input"])
    plan_cfg = _run_plan_from_input(lesson)
    raw_curriculum = state.get("curriculum_analysis") or {}
    curriculum = CurriculumAnalysis.model_validate(
        raw_curriculum
        if raw_curriculum
        else {
            "core_competencies": [],
            "academic_requirements": [],
            "content_points": [],
            "confidence": "low",
        }
    )
    plan = LessonPlan.model_validate(state["lesson_plan"])
    qa = review_lesson_plan(lesson, plan, curriculum)
    revise_count = int(state.get("lesson_plan_revise_count") or 0)
    downstream = _materials_label(plan_cfg.material_agents) if plan_cfg.run_materials else "结束"

    if qa.passed:
        if plan_cfg.run_materials and PLAN_CONFIRM_GATE:
            notes = (qa.notes or "") + "；教案审核通过 → 等待老师确认后再并行生成"
            report_progress("lesson_plan_review", "教案审核通过 → 请老师确认")
            safe_log("[教案审核员] 通过 → 等待老师确认")
        elif plan_cfg.run_materials:
            notes = (qa.notes or "") + f"；教案审核通过 → 并行生成：{downstream}"
            report_progress("lesson_plan_review", f"教案审核通过 → 并行生成：{downstream}")
            safe_log(f"[教案审核员] 通过 → 并行：{downstream}")
        else:
            notes = (qa.notes or "") + "；教案审核通过 → 本场景无下游材料，流程结束"
            report_progress("lesson_plan_review", "教案审核通过 → 本场景结束")
            safe_log("[教案审核员] 通过 → 无下游材料")
        if revise_count > 0:
            notes += f"；此前已打回修改 {revise_count} 次"
        qa = qa.model_copy(update={"revised": revise_count > 0, "notes": notes})
    else:
        can_reject = revise_count < MAX_LESSON_PLAN_REVISES
        if can_reject:
            qa = qa.model_copy(
                update={
                    "revised": False,
                    "notes": (qa.notes or "") + "；审核不通过，打回教案设计师修改",
                }
            )
            report_progress("lesson_plan_review", "教案审核不通过 → 打回教案设计师")
            safe_log(f"[教案审核员] 不通过: {qa.issues} → 打回设计师")
        else:
            cont = f"继续并行生成：{downstream}" if plan_cfg.run_materials else "结束流程"
            qa = qa.model_copy(
                update={
                    "revised": True,
                    "notes": (qa.notes or "")
                    + f"；已打回修改 {revise_count} 次仍未完全通过，{cont}",
                }
            )
            report_progress("lesson_plan_review", f"复审仍有问题，{cont}")
            safe_log(f"[教案审核员] 复审仍未通过，放行: {qa.issues}")

    _gap()
    return {"lesson_plan_qa": qa.model_dump()}


def _route_after_lesson_review(state: PrepState) -> Literal["revise", "continue"]:
    qa_raw = state.get("lesson_plan_qa") or {}
    passed = bool(qa_raw.get("passed"))
    revise_count = int(state.get("lesson_plan_revise_count") or 0)
    if (not passed) and revise_count < MAX_LESSON_PLAN_REVISES:
        return "revise"
    return "continue"


def _run_lesson_plan_with_review(state: PrepState, on_checkpoint) -> PrepState:
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    state.update(_lesson_plan_design_node(state))
    if on_checkpoint:
        on_checkpoint(_state_to_result(state))

    if not plan_cfg.run_lesson_review:
        # 快速草稿等场景：跳过审核，直接放行
        state["lesson_plan_qa"] = {
            "passed": True,
            "issues": [],
            "suggested_fixes": [],
            "revised": False,
            "notes": "本场景未启用教案审核员，跳过质检",
        }
        if on_checkpoint:
            on_checkpoint(_state_to_result(state))
        _commit_teaching_anchors(state, announce=True)
        if on_checkpoint:
            on_checkpoint(_state_to_result(state))
        return state

    while True:
        state.update(_lesson_plan_review_node(state))
        if on_checkpoint:
            on_checkpoint(_state_to_result(state))
        if _route_after_lesson_review(state) == "continue":
            break
        state.update(_lesson_plan_revise_node(state))
        if on_checkpoint:
            on_checkpoint(_state_to_result(state))
    _commit_teaching_anchors(state, announce=True)
    if on_checkpoint:
        on_checkpoint(_state_to_result(state))
    return state


def _design_exercises(
    lesson: LessonInput,
    plan: LessonPlan,
    anchors: list[TeachingAnchor],
) -> dict:
    safe_log("[习题组卷师] 先读取教学三元组，再并行生成...")
    paper = run_exercise_agent(lesson, plan, anchors)
    # 并行路径下不做节点内质检，交给一致性检查员
    return {"exercise_paper": paper.model_dump(), "exercise_qa": None}


def _design_slides(
    lesson: LessonInput,
    plan: LessonPlan,
    anchors: list[TeachingAnchor],
) -> dict:
    safe_log("[课件生成师] 先读取教学三元组，再并行生成...")
    slides = run_slides_agent(lesson, plan, anchors)
    return {"slides": slides.model_dump(), "slides_qa": None}


def _design_blackboard(
    lesson: LessonInput,
    plan: LessonPlan,
    anchors: list[TeachingAnchor],
    *,
    weaken: bool = False,
) -> dict:
    safe_log("[板书设计师] 先读取教学三元组，再并行生成...")
    board = run_blackboard_agent(lesson, plan, anchors)
    if weaken and MOCK_LLM:
        # 故意不对齐环节，触发一致性检查打回
        board = board.model_copy(update={"linked_stages": ["未对齐环节"], "main_board": board.main_board})
    return {"blackboard": board.model_dump()}


def _parallel_materials(
    state: PrepState,
    *,
    only: set[str] | None = None,
    weaken_for_mock: bool = False,
) -> dict:
    """并行运行已启用的材料 Agent（可指定子集），并上报分路进度。"""
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    anchors = _commit_teaching_anchors(state)
    plan_cfg = _run_plan_from_input(lesson)
    default_targets = set(plan_cfg.material_agents) or set(material_plugin_ids())
    targets = set(only) if only is not None else default_targets
    # 单路重跑时也尊重插件白名单
    targets &= set(material_plugin_ids())
    if not targets:
        report_progress("materials", "本场景未启用任何材料 Agent，跳过并行生成")
        return {"materials_lanes": {}}

    label = _materials_label(targets)
    full = set(plan_cfg.material_agents) or set(material_plugin_ids())
    if targets != full:
        seed_materials_lanes(state.get("materials_lanes") or get_materials_lanes())
        lanes = reset_materials_lanes(targets, clear_others=False)
    else:
        lanes = reset_materials_lanes(targets)
    report_progress("materials", f"并行生成：{label}", {"lanes": lanes})
    safe_log(f"[并行] 启动设计师：{sorted(targets)}")

    designers: dict[str, Callable[[], dict]] = {
        "exercises": lambda: _design_exercises(lesson, plan, anchors),
        "slides": lambda: _design_slides(lesson, plan, anchors),
        "blackboard": lambda: _design_blackboard(
            lesson, plan, anchors, weaken=weaken_for_mock and "blackboard" in targets
        ),
    }
    jobs = {name: designers[name] for name in targets if name in designers}

    for name in jobs:
        set_lane_status(name, "running")

    merged: dict[str, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                merged.update(fut.result())
                set_lane_status(name, "done")
                safe_log(f"[并行] {name} 完成")
            except Exception as exc:  # noqa: BLE001
                err = f"{name} 生成失败: {exc}"
                set_lane_status(name, "error", error=str(exc))
                safe_log(f"[并行] {err}")
                errors.append(err)

    final_lanes = get_materials_lanes()
    if errors and len(errors) == len(jobs):
        report_progress(
            "materials",
            f"并行生成全部失败（{label}）",
            {"lanes": final_lanes},
        )
        raise RuntimeError("；".join(errors))

    msg = f"并行生成完成（{label}）"
    if errors:
        msg = f"并行生成部分完成（失败 {len(errors)}）：{label}"
    report_progress("materials", msg, {"lanes": final_lanes})
    _gap()
    out = dict(merged)
    out["materials_lanes"] = final_lanes
    if errors:
        out["errors"] = errors
        out["materials_failed"] = [
            k for k, v in final_lanes.items() if v.get("status") == "error"
        ]
    return out


def _consistency_check_node(state: PrepState) -> dict:
    report_progress("consistency", "一致性检查员工作中")
    safe_log("[一致性检查员] 对照教案检查已启用材料...")
    lesson = LessonInput.model_validate(state["input"])
    plan_cfg = _run_plan_from_input(lesson)
    active = set(plan_cfg.material_agents)
    if state.get("exercise_paper"):
        active.add("exercises")
    if state.get("slides"):
        active.add("slides")
    if state.get("blackboard"):
        active.add("blackboard")
    active &= set(material_plugin_ids())

    plan = LessonPlan.model_validate(state["lesson_plan"])
    paper = ExercisePaper.model_validate(
        state.get("exercise_paper") or {"items": [{"index": 1, "stem": "占位"}]}
    )
    slides = Slides.model_validate(state.get("slides") or {"pages": []})
    board = Blackboard.model_validate(state.get("blackboard") or {})
    anchors = _anchors_from_state(state)
    qa = review_consistency(
        lesson,
        plan,
        paper,
        slides,
        board,
        active_modules=active or None,
        anchors=anchors,
    )
    revise_count = int(state.get("consistency_revise_count") or 0)

    if qa.passed:
        notes = (qa.notes or "") + "；材料一致，备课完成"
        if revise_count > 0:
            notes += f"；此前已打回修改 {revise_count} 次"
        qa = qa.model_copy(update={"revised": revise_count > 0, "notes": notes})
        report_progress("consistency", "一致性检查通过")
        safe_log("[一致性检查员] 通过")
    else:
        can_reject = revise_count < MAX_CONSISTENCY_REVISES
        if can_reject:
            mods = "、".join(qa.conflict_modules) or "相关设计师"
            qa = qa.model_copy(
                update={
                    "revised": False,
                    "notes": (qa.notes or "") + f"；发现冲突，打回修改：{mods}",
                }
            )
            report_progress("consistency", f"不一致 → 打回 {mods}")
            safe_log(f"[一致性检查员] 冲突: {qa.issues} → 打回 {qa.conflict_modules}")
        else:
            qa = qa.model_copy(
                update={
                    "revised": True,
                    "notes": (qa.notes or "")
                    + f"；已打回修改 {revise_count} 次仍有问题，结束流程",
                }
            )
            report_progress("consistency", "复检仍有冲突，结束流程")
            safe_log(f"[一致性检查员] 复检仍未通过，放行结束: {qa.issues}")

    _gap()
    return {"consistency_qa": qa.model_dump()}


def _route_after_consistency(state: PrepState) -> Literal["revise", "continue"]:
    qa = state.get("consistency_qa") or {}
    if bool(qa.get("passed")):
        return "continue"
    if int(state.get("consistency_revise_count") or 0) < MAX_CONSISTENCY_REVISES:
        return "revise"
    return "continue"


def _consistency_revise_node(state: PrepState) -> dict:
    """按冲突模块并行打回各设计师修改。"""
    qa = ConsistencyReport.model_validate(state.get("consistency_qa") or {})
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    modules = set(qa.conflict_modules or [])
    if not modules:
        modules = set(plan_cfg.material_agents) or set(material_plugin_ids())
    modules &= set(plan_cfg.material_agents) or set(material_plugin_ids())
    report_progress("materials", f"按一致性意见修改：{'、'.join(sorted(modules))}")
    safe_log(f"[一致性打回] 修改模块: {sorted(modules)}")

    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    anchors = _anchors_from_state(state)
    issues = qa.issues or []
    count = int(state.get("consistency_revise_count") or 0) + 1
    updates: dict[str, Any] = {"consistency_revise_count": count}

    reset_materials_lanes(modules, clear_others=False)
    for name in modules:
        set_lane_status(name, "running")

    def _fix_ex() -> dict:
        paper = ExercisePaper.model_validate(state["exercise_paper"])
        from lesson_prep.schemas import LessonPlanQAReport as QA

        fixed = revise_exercise_paper(
            lesson,
            plan,
            paper,
            QA(passed=False, issues=issues, suggested_fixes=qa.suggested_fixes),
            anchors=anchors,
        )
        return {"exercise_paper": fixed.model_dump()}

    def _fix_sl() -> dict:
        slides = Slides.model_validate(state["slides"])
        from lesson_prep.schemas import LessonPlanQAReport as QA

        fixed = revise_slides(
            lesson,
            plan,
            slides,
            QA(passed=False, issues=issues, suggested_fixes=qa.suggested_fixes),
            anchors=anchors,
        )
        return {"slides": fixed.model_dump()}

    def _fix_bb() -> dict:
        board = Blackboard.model_validate(state["blackboard"])
        fixed = revise_blackboard(lesson, plan, board, issues=issues, anchors=anchors)
        return {"blackboard": fixed.model_dump()}

    jobs: dict[str, Callable[[], dict]] = {}
    if "exercises" in modules and state.get("exercise_paper"):
        jobs["exercises"] = _fix_ex
    if "slides" in modules and state.get("slides"):
        jobs["slides"] = _fix_sl
    if "blackboard" in modules and state.get("blackboard"):
        jobs["blackboard"] = _fix_bb

    with ThreadPoolExecutor(max_workers=max(1, len(jobs) or 1)) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                updates.update(fut.result())
                set_lane_status(name, "done")
                safe_log(f"[一致性打回] {name} 修改完成")
            except Exception as exc:  # noqa: BLE001
                set_lane_status(name, "error", error=str(exc))
                safe_log(f"[一致性打回] {name} 修改失败: {exc}")

    updates["materials_lanes"] = get_materials_lanes()
    report_progress(
        "materials",
        "冲突模块已修改 → 再次一致性检查",
        {"lanes": updates["materials_lanes"]},
    )
    _gap()
    return updates


def _extract_anchors_node(state: PrepState) -> dict:
    """教案定稿后提取教学三元组。"""
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    updates: dict[str, Any] = {}
    working = dict(state)
    if not working.get("lesson_plan_qa") and not plan_cfg.run_lesson_review:
        updates["lesson_plan_qa"] = {
            "passed": True,
            "issues": [],
            "suggested_fixes": [],
            "revised": False,
            "notes": "本场景未启用教案审核员，跳过质检",
        }
        working.update(updates)
    anchors = _commit_teaching_anchors(working, announce=True)
    updates["teaching_anchors"] = [item.model_dump() for item in anchors]
    return updates


def _plan_confirm_gate_node(state: PrepState) -> dict:
    """老师确认闸门：LangGraph interrupt，resume 后可带回编辑后的教案。"""
    from langgraph.types import interrupt

    plan_cfg = _run_plan_from_input(state.get("input") or {})
    force = state.get("pause_after_plan")
    should_pause = PLAN_CONFIRM_GATE if force is None else bool(force)

    if not plan_cfg.run_materials or not should_pause:
        return {"awaiting_plan_confirm": False}

    mats = _materials_label(plan_cfg.material_agents)
    report_progress(
        "awaiting_plan_confirm",
        f"教案已就绪：请老师确认或修改后继续生成（{mats}）",
    )
    safe_log("[闸门] 等待老师确认教案 (LangGraph interrupt)")

    resume_payload = interrupt(
        {
            "type": "awaiting_plan_confirm",
            "message": f"教案已就绪：请确认后继续生成（{mats}）",
        }
    )

    updates: dict[str, Any] = {"awaiting_plan_confirm": False}
    if isinstance(resume_payload, dict):
        if resume_payload.get("lesson_plan") is not None:
            updates["lesson_plan"] = resume_payload["lesson_plan"]
        if resume_payload.get("teaching_anchors") is not None:
            updates["teaching_anchors"] = resume_payload["teaching_anchors"]
        if resume_payload.get("lesson_plan_qa") is not None:
            updates["lesson_plan_qa"] = resume_payload["lesson_plan_qa"]

    # 老师改稿后刷新三元组
    merged = {**state, **updates}
    if updates.get("lesson_plan") is not None:
        anchors = _commit_teaching_anchors(merged, announce=True)
        updates["teaching_anchors"] = [item.model_dump() for item in anchors]
    return updates


def _materials_node(state: PrepState) -> dict:
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    only_raw = state.get("materials_only")
    only = set(only_raw) if only_raw else None
    weaken = MOCK_LLM and int(state.get("consistency_revise_count") or 0) == 0
    return _parallel_materials(
        state,
        only=only,
        weaken_for_mock=weaken
        and only is None
        and "blackboard" in plan_cfg.material_agents,
    )


def _finish_node(state: PrepState) -> dict:
    report_progress("done", "备课完成")
    return {"awaiting_plan_confirm": False}


def _route_entry(
    state: PrepState,
) -> Literal[
    "curriculum",
    "lesson_design",
    "extract_anchors",
    "materials",
    "consistency",
    "finish",
]:
    start = state.get("start_at") or "curriculum"
    plan_cfg = _run_plan_from_input(state.get("input") or {})

    if start == "curriculum":
        if plan_cfg.run_curriculum:
            return "curriculum"
        start = "lesson_plan"

    if start == "lesson_plan":
        if plan_cfg.run_lesson_plan:
            return "lesson_design"
        if state.get("lesson_plan"):
            return "extract_anchors"
        return "finish"

    if start in {"materials", "exercises", "slides", "blackboard"}:
        if plan_cfg.run_materials or start != "materials" or state.get("materials_only"):
            return "materials"
        return "finish"

    if start == "consistency":
        if plan_cfg.run_consistency:
            return "consistency"
        return "finish"

    return "finish"


def _route_after_curriculum(state: PrepState) -> Literal["lesson_design", "finish"]:
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    if plan_cfg.run_lesson_plan:
        return "lesson_design"
    return "finish"


def _route_after_lesson_design(state: PrepState) -> Literal["review", "anchors"]:
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    if plan_cfg.run_lesson_review:
        return "review"
    return "anchors"


def _route_after_anchors(state: PrepState) -> Literal["gate", "finish"]:
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    # 显式从材料/一致性重跑时不进闸门（通常也不会经过本节点）
    if (state.get("start_at") or "") in {
        "materials",
        "exercises",
        "slides",
        "blackboard",
        "consistency",
    }:
        return "finish"
    if plan_cfg.run_materials:
        return "gate"
    return "finish"


def _route_after_gate(state: PrepState) -> Literal["materials", "finish"]:
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    if plan_cfg.run_materials:
        return "materials"
    return "finish"


def _route_after_materials(state: PrepState) -> Literal["consistency", "finish"]:
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    if plan_cfg.run_consistency:
        return "consistency"
    return "finish"


def _route_after_consistency_lg(state: PrepState) -> Literal["revise", "finish"]:
    if _route_after_consistency(state) == "revise":
        return "revise"
    return "finish"


_compiled_graph = None
_compiled_lock = __import__("threading").Lock()


def build_graph(checkpointer=None):
    """正式 LangGraph 编排：课标 → 教案 ⇄ 审核 → 闸门 → 并行材料 ⇄ 一致性。"""
    try:
        from langgraph.graph import END, START, StateGraph
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "未安装 langgraph。请使用项目虚拟环境安装 requirements.txt"
        ) from exc

    graph = StateGraph(PrepState)
    graph.add_node("curriculum_analyst", _curriculum_node)
    graph.add_node("lesson_designer", _lesson_plan_design_node)
    graph.add_node("lesson_plan_reviewer", _lesson_plan_review_node)
    graph.add_node("lesson_reviser", _lesson_plan_revise_node)
    graph.add_node("extract_anchors", _extract_anchors_node)
    graph.add_node("plan_confirm_gate", _plan_confirm_gate_node)
    graph.add_node("materials_parallel", _materials_node)
    graph.add_node("consistency_checker", _consistency_check_node)
    graph.add_node("consistency_reviser", _consistency_revise_node)
    graph.add_node("finish", _finish_node)

    graph.add_conditional_edges(
        START,
        _route_entry,
        {
            "curriculum": "curriculum_analyst",
            "lesson_design": "lesson_designer",
            "extract_anchors": "extract_anchors",
            "materials": "materials_parallel",
            "consistency": "consistency_checker",
            "finish": "finish",
        },
    )
    graph.add_conditional_edges(
        "curriculum_analyst",
        _route_after_curriculum,
        {"lesson_design": "lesson_designer", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "lesson_designer",
        _route_after_lesson_design,
        {"review": "lesson_plan_reviewer", "anchors": "extract_anchors"},
    )
    graph.add_conditional_edges(
        "lesson_plan_reviewer",
        _route_after_lesson_review,
        {"revise": "lesson_reviser", "continue": "extract_anchors"},
    )
    graph.add_edge("lesson_reviser", "lesson_plan_reviewer")
    graph.add_conditional_edges(
        "extract_anchors",
        _route_after_anchors,
        {"gate": "plan_confirm_gate", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "plan_confirm_gate",
        _route_after_gate,
        {"materials": "materials_parallel", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "materials_parallel",
        _route_after_materials,
        {"consistency": "consistency_checker", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "consistency_checker",
        _route_after_consistency_lg,
        {"revise": "consistency_reviser", "finish": "finish"},
    )
    graph.add_edge("consistency_reviser", "consistency_checker")
    graph.add_edge("finish", END)

    if checkpointer is None:
        from lesson_prep.checkpointing import get_checkpointer

        checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)


def get_graph():
    """编译后的单例图（带 checkpointer）。"""
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph
    with _compiled_lock:
        if _compiled_graph is None:
            _compiled_graph = build_graph()
        return _compiled_graph


def _state_to_result(state: dict[str, Any]) -> dict[str, Any]:
    plan = state.get("agent_plan")
    if not plan and state.get("input"):
        plan = _run_plan_from_input(state["input"]).to_dict()
    return {
        "input": state.get("input"),
        "agent_plan": plan,
        "curriculum_analysis": state.get("curriculum_analysis"),
        "lesson_plan": state.get("lesson_plan"),
        "lesson_plan_qa": state.get("lesson_plan_qa"),
        "lesson_plan_revise_count": state.get("lesson_plan_revise_count", 0),
        "teaching_anchors": state.get("teaching_anchors"),
        "exercise_paper": state.get("exercise_paper"),
        "exercise_qa": state.get("exercise_qa"),
        "slides": state.get("slides"),
        "slides_qa": state.get("slides_qa"),
        "blackboard": state.get("blackboard"),
        "consistency_qa": state.get("consistency_qa"),
        "consistency_revise_count": state.get("consistency_revise_count", 0),
        "materials_lanes": state.get("materials_lanes") or get_materials_lanes() or None,
        "materials_failed": state.get("materials_failed"),
        "awaiting_plan_confirm": state.get("awaiting_plan_confirm"),
        "retrieved_context": state.get("retrieved_context"),
        "errors": state.get("errors", []),
        "thread_id": state.get("thread_id"),
    }


def _prepare_state(
    lesson_input: dict[str, Any],
    *,
    resume_from: str | None,
    prior_state: dict[str, Any] | None,
    pause_after_plan: bool | None = None,
) -> dict[str, Any]:
    run_plan = _run_plan_from_input(lesson_input)
    start_at = resume_from or "curriculum"
    materials_only: list[str] | None = None
    if resume_from in {"exercises", "slides", "blackboard"}:
        materials_only = [resume_from]
        start_at = "materials"

    state: dict[str, Any] = {
        "input": lesson_input,
        "agent_plan": run_plan.to_dict(),
        "errors": [],
        "lesson_plan_revise_count": 0,
        "consistency_revise_count": 0,
        "start_at": start_at,
        "pause_after_plan": pause_after_plan,
        "awaiting_plan_confirm": False,
    }
    if materials_only:
        state["materials_only"] = materials_only

    if prior_state:
        for key in (
            "curriculum_analysis",
            "lesson_plan",
            "lesson_plan_qa",
            "lesson_plan_revise_count",
            "teaching_anchors",
            "exercise_paper",
            "exercise_qa",
            "slides",
            "slides_qa",
            "blackboard",
            "consistency_qa",
            "consistency_revise_count",
            "retrieved_context",
            "agent_plan",
            "materials_lanes",
        ):
            if prior_state.get(key) is not None:
                state[key] = prior_state[key]
        state["agent_plan"] = run_plan.to_dict()

    if resume_from:
        if resume_from not in _STEP_OUTPUT_KEYS:
            raise ValueError(f"不支持的重跑起点: {resume_from}")
        for key in _STEP_OUTPUT_KEYS[resume_from]:
            state.pop(key, None)
        if resume_from == "lesson_plan":
            state["lesson_plan_revise_count"] = 0
        if resume_from in {"materials", "exercises", "slides", "blackboard", "consistency"}:
            state["consistency_revise_count"] = 0

        need: dict[str, list[str]] = {
            "lesson_plan": ["curriculum_analysis"],
            "materials": ["lesson_plan"],
            "exercises": ["lesson_plan"],
            "slides": ["lesson_plan"],
            "blackboard": ["lesson_plan"],
            "consistency": ["lesson_plan"],
        }
        if resume_from == "lesson_plan" and run_plan.run_curriculum:
            need["lesson_plan"] = ["curriculum_analysis"]
        elif resume_from == "lesson_plan":
            need["lesson_plan"] = []

        for req in need.get(resume_from, []):
            if not state.get(req):
                raise ValueError(f"从「{resume_from}」重跑需要已有 {req}，请改从更早步骤重跑")
    return state


def _has_interrupt(payload: Any) -> bool:
    if isinstance(payload, dict) and payload.get("__interrupt__"):
        return True
    return False


def run_preparation(
    lesson_input: dict,
    on_progress=None,
    *,
    resume_from: PipelineStep | None = None,
    prior_state: dict[str, Any] | None = None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    pause_after_plan: bool | None = None,
    thread_id: str | None = None,
    resume_value: Any = None,
) -> dict:
    """
    正式走 LangGraph：
      课标 → 教案 ⇄ 审核 → extract_anchors → [interrupt 闸门]
           → 并行材料 ⇄ 一致性

    resume_value:
      非 None 时表示对已中断 thread 执行 Command(resume=...)
    """
    import uuid

    from langgraph.types import Command

    set_progress_callback(on_progress)
    tid = thread_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": tid}}

    try:
        clear_materials_lanes()
        graph = get_graph()
        plan_cfg = _run_plan_from_input(lesson_input)
        safe_log(f"[装配] profile={plan_cfg.profile_id} agents={plan_cfg.agents} thread={tid}")

        def _emit_checkpoint(values: dict[str, Any]) -> None:
            if on_checkpoint:
                payload = _state_to_result(values)
                payload["thread_id"] = tid
                on_checkpoint(payload)

        if resume_value is not None:
            safe_log("[闸门] 恢复 LangGraph interrupt")
            out = graph.invoke(Command(resume=resume_value), config)
        else:
            state = _prepare_state(
                lesson_input,
                resume_from=resume_from,
                prior_state=prior_state if (resume_from or prior_state) else None,
                pause_after_plan=pause_after_plan,
            )
            if resume_from:
                safe_log(f"从步骤重跑: {resume_from}")
            out = graph.invoke(state, config)

        # 中断：返回当前状态，标记等待确认
        if _has_interrupt(out):
            snap = graph.get_state(config)
            values = dict(snap.values or {})
            result = _state_to_result(values)
            result["awaiting_plan_confirm"] = True
            result["thread_id"] = tid
            _emit_checkpoint(result)
            return result

        # 正常结束：再读一次 state，确保 lanes 完整
        try:
            values = dict(graph.get_state(config).values or {})
            if values:
                out = {**values, **(out if isinstance(out, dict) else {})}
        except Exception:  # noqa: BLE001
            pass

        if isinstance(out, dict):
            out.pop("__interrupt__", None)
        result = _state_to_result(out if isinstance(out, dict) else {})
        result["awaiting_plan_confirm"] = False
        result["thread_id"] = tid
        report_progress("done", "备课完成")
        _emit_checkpoint(result)
        return result
    finally:
        clear_materials_lanes()
        set_progress_callback(None)


# 兼容旧导入名
PIPELINE_STEPS = MAIN_PIPELINE
