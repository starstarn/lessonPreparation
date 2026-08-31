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
    return state


def _design_exercises(lesson: LessonInput, plan: LessonPlan) -> dict:
    safe_log("[习题组卷师] 并行生成中...")
    paper = run_exercise_agent(lesson, plan)
    # 并行路径下不做节点内质检，交给一致性检查员
    return {"exercise_paper": paper.model_dump(), "exercise_qa": None}


def _design_slides(lesson: LessonInput, plan: LessonPlan) -> dict:
    safe_log("[课件生成师] 并行生成中...")
    slides = run_slides_agent(lesson, plan)
    return {"slides": slides.model_dump(), "slides_qa": None}


def _design_blackboard(lesson: LessonInput, plan: LessonPlan, *, weaken: bool = False) -> dict:
    safe_log("[板书设计师] 并行生成中...")
    board = run_blackboard_agent(lesson, plan)
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
        "exercises": lambda: _design_exercises(lesson, plan),
        "slides": lambda: _design_slides(lesson, plan),
        "blackboard": lambda: _design_blackboard(
            lesson, plan, weaken=weaken_for_mock and "blackboard" in targets
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
    qa = review_consistency(
        lesson, plan, paper, slides, board, active_modules=active or None
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
        )
        return {"slides": fixed.model_dump()}

    def _fix_bb() -> dict:
        board = Blackboard.model_validate(state["blackboard"])
        fixed = revise_blackboard(lesson, plan, board, issues=issues)
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


def _run_materials_with_consistency(
    state: PrepState,
    on_checkpoint,
    *,
    only: set[str] | None = None,
) -> PrepState:
    plan_cfg = _run_plan_from_input(state.get("input") or {})
    weaken = MOCK_LLM and int(state.get("consistency_revise_count") or 0) == 0
    state.update(
        _parallel_materials(
            state,
            only=only,
            weaken_for_mock=weaken and only is None and "blackboard" in plan_cfg.material_agents,
        )
    )
    if on_checkpoint:
        on_checkpoint(_state_to_result(state))

    if not plan_cfg.run_consistency:
        report_progress("materials", "本场景未启用一致性检查，跳过")
        return state

    while True:
        state.update(_consistency_check_node(state))
        if on_checkpoint:
            on_checkpoint(_state_to_result(state))
        if _route_after_consistency(state) == "continue":
            break
        state.update(_consistency_revise_node(state))
        if on_checkpoint:
            on_checkpoint(_state_to_result(state))
    return state


def build_graph():
    """课标 → 教案 ⇄ 审核 → 并行(课件/习题/板书) ⇄ 一致性检查。"""
    try:
        from langgraph.graph import END, START, StateGraph
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "未安装 langgraph。请使用项目虚拟环境："
            " .\\.venv\\Scripts\\python -m pip install -r requirements.txt"
        ) from exc

    def _materials_node(state: PrepState) -> dict:
        return _parallel_materials(state)

    graph = StateGraph(PrepState)
    graph.add_node("curriculum_analyst", _curriculum_node)
    graph.add_node("lesson_designer", _lesson_plan_design_node)
    graph.add_node("lesson_plan_reviewer", _lesson_plan_review_node)
    graph.add_node("lesson_reviser", _lesson_plan_revise_node)
    graph.add_node("materials_parallel", _materials_node)
    graph.add_node("consistency_checker", _consistency_check_node)
    graph.add_node("consistency_reviser", _consistency_revise_node)

    graph.add_edge(START, "curriculum_analyst")
    graph.add_edge("curriculum_analyst", "lesson_designer")
    graph.add_edge("lesson_designer", "lesson_plan_reviewer")
    graph.add_conditional_edges(
        "lesson_plan_reviewer",
        _route_after_lesson_review,
        {"revise": "lesson_reviser", "continue": "materials_parallel"},
    )
    graph.add_edge("lesson_reviser", "lesson_plan_reviewer")
    graph.add_edge("materials_parallel", "consistency_checker")
    graph.add_conditional_edges(
        "consistency_checker",
        _route_after_consistency,
        {"revise": "consistency_reviser", "continue": END},
    )
    graph.add_edge("consistency_reviser", "consistency_checker")
    return graph.compile()


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
    }


def _prepare_state(
    lesson_input: dict[str, Any],
    *,
    resume_from: str | None,
    prior_state: dict[str, Any] | None,
) -> dict[str, Any]:
    run_plan = _run_plan_from_input(lesson_input)
    state: dict[str, Any] = {
        "input": lesson_input,
        "agent_plan": run_plan.to_dict(),
        "errors": [],
        "lesson_plan_revise_count": 0,
        "consistency_revise_count": 0,
    }
    if prior_state:
        for key in (
            "curriculum_analysis",
            "lesson_plan",
            "lesson_plan_qa",
            "lesson_plan_revise_count",
            "exercise_paper",
            "exercise_qa",
            "slides",
            "slides_qa",
            "blackboard",
            "consistency_qa",
            "consistency_revise_count",
            "retrieved_context",
            "agent_plan",
        ):
            if prior_state.get(key) is not None:
                state[key] = prior_state[key]
        # 以当前 input 的装配为准，覆盖 prior
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
        # 若场景启用了课标，重跑教案时仍要求有课标分析
        if resume_from == "lesson_plan" and run_plan.run_curriculum:
            need["lesson_plan"] = ["curriculum_analysis"]
        elif resume_from == "lesson_plan":
            need["lesson_plan"] = []

        for req in need.get(resume_from, []):
            if not state.get(req):
                raise ValueError(f"从「{resume_from}」重跑需要已有 {req}，请改从更早步骤重跑")
    return state


def run_preparation(
    lesson_input: dict,
    on_progress=None,
    *,
    resume_from: PipelineStep | None = None,
    prior_state: dict[str, Any] | None = None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    pause_after_plan: bool | None = None,
) -> dict:
    """
    按 Agent 插件装配计划调度：
      课标 → 教案设计师 ⇄ 教案审核员
           → [可选：老师确认闸门]
           → 并行(已启用材料) ⇄ 一致性检查员

    pause_after_plan:
      None → 跟随 PLAN_CONFIRM_GATE，且仅在有下游材料时暂停
      True/False → 强制开/关本次暂停
    """
    set_progress_callback(on_progress)
    try:
        clear_materials_lanes()
        state = _prepare_state(
            lesson_input,
            resume_from=resume_from,
            prior_state=prior_state if (resume_from or prior_state) else None,
        )
        plan_cfg = _run_plan_from_input(lesson_input)
        safe_log(
            f"[装配] profile={plan_cfg.profile_id} agents={plan_cfg.agents}"
        )
        if resume_from:
            safe_log(f"从步骤重跑: {resume_from}")

        # 规范化起点
        start = resume_from or "curriculum"
        if start == "curriculum":
            if plan_cfg.run_curriculum:
                state.update(_curriculum_node(state))
                if on_checkpoint:
                    on_checkpoint(_state_to_result(state))
            else:
                safe_log("[装配] 跳过课标解读员")
            start = "lesson_plan"

        if start == "lesson_plan":
            if plan_cfg.run_lesson_plan:
                state = _run_lesson_plan_with_review(state, on_checkpoint)
                should_pause = (
                    PLAN_CONFIRM_GATE if pause_after_plan is None else pause_after_plan
                )
                # 无下游材料时不必开闸门
                if should_pause and plan_cfg.run_materials:
                    state["awaiting_plan_confirm"] = True
                    mats = _materials_label(plan_cfg.material_agents)
                    report_progress(
                        "awaiting_plan_confirm",
                        f"教案已就绪：请老师确认或修改后继续生成（{mats}）",
                    )
                    safe_log("[闸门] 等待老师确认教案")
                    if on_checkpoint:
                        on_checkpoint(_state_to_result(state))
                    return _state_to_result(state)
            else:
                safe_log("[装配] 跳过教案设计师")
            start = "materials"

        state.pop("awaiting_plan_confirm", None)

        if start in {"materials", "exercises", "slides", "blackboard"}:
            only = None
            if start in {"exercises", "slides", "blackboard"}:
                only = {start}
            if plan_cfg.run_materials or only:
                state = _run_materials_with_consistency(state, on_checkpoint, only=only)
            else:
                safe_log("[装配] 跳过材料并行生成")
        elif start == "consistency":
            if plan_cfg.run_consistency:
                state["consistency_revise_count"] = 0
                while True:
                    state.update(_consistency_check_node(state))
                    if on_checkpoint:
                        on_checkpoint(_state_to_result(state))
                    if _route_after_consistency(state) == "continue":
                        break
                    state.update(_consistency_revise_node(state))
                    if on_checkpoint:
                        on_checkpoint(_state_to_result(state))
            else:
                safe_log("[装配] 跳过一致性检查")

        report_progress("done", "备课完成")
        out = _state_to_result(state)
        out["awaiting_plan_confirm"] = False
        return out
    finally:
        clear_materials_lanes()
        set_progress_callback(None)


# 兼容旧导入名
PIPELINE_STEPS = MAIN_PIPELINE
