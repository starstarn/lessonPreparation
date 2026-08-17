from __future__ import annotations

import time
from typing import Any, Callable, Literal

from lesson_prep.agents import (
    revise_exercise_paper,
    revise_lesson_plan,
    revise_slides,
    review_exercise_paper,
    review_lesson_plan,
    review_slides,
    run_blackboard_agent,
    run_curriculum_agent,
    run_exercise_agent,
    run_lesson_plan_agent,
    run_slides_agent,
)
from lesson_prep.config import MOCK_LLM
from lesson_prep.logutil import safe_log
from lesson_prep.progress import report_progress, set_progress_callback
from lesson_prep.schemas import (
    CurriculumAnalysis,
    ExercisePaper,
    LessonInput,
    LessonPlan,
    LessonPlanQAReport,
    Slides,
)
from lesson_prep.state import PrepState

# 重跑入口仍按「设计师」节点；教案审核是设计后的条件回路
PipelineStep = Literal["curriculum", "lesson_plan", "exercises", "slides", "blackboard"]

PIPELINE_STEPS: list[PipelineStep] = [
    "curriculum",
    "lesson_plan",
    "exercises",
    "slides",
    "blackboard",
]

MAX_LESSON_PLAN_REVISES = 1

_STEP_OUTPUT_KEYS: dict[PipelineStep, list[str]] = {
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
    ],
    "exercises": ["exercise_paper", "exercise_qa", "slides", "slides_qa", "blackboard"],
    "slides": ["slides", "slides_qa", "blackboard"],
    "blackboard": ["blackboard"],
}


def _gap() -> None:
    if not MOCK_LLM:
        time.sleep(2)


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
    """教案设计师：只写初稿（不审核）。"""
    report_progress("lesson_plan", "教案设计师撰写教案")
    safe_log("[教案设计师] 撰写初稿...")
    lesson = LessonInput.model_validate(state["input"])
    curriculum = CurriculumAnalysis.model_validate(state["curriculum_analysis"])
    plan = run_lesson_plan_agent(lesson, curriculum)
    safe_log("[教案设计师] 初稿完成 → 送交教案审核员")
    _gap()
    return {
        "lesson_plan": plan.model_dump(),
        "lesson_plan_revise_count": 0,
    }


def _lesson_plan_revise_node(state: PrepState) -> dict:
    """教案设计师：按审核员意见修改（打回路径）。"""
    report_progress("lesson_plan", "教案设计师按审核意见修改中")
    safe_log("[教案设计师] 收到审核打回，修改中...")
    lesson = LessonInput.model_validate(state["input"])
    curriculum = CurriculumAnalysis.model_validate(state["curriculum_analysis"])
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
    """教案审核员：只审核，不直接改教案。"""
    report_progress("lesson_plan_review", "教案审核员审核中")
    safe_log("[教案审核员] 审核中...")
    lesson = LessonInput.model_validate(state["input"])
    curriculum = CurriculumAnalysis.model_validate(state["curriculum_analysis"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    qa = review_lesson_plan(lesson, plan, curriculum)
    revise_count = int(state.get("lesson_plan_revise_count") or 0)

    if qa.passed:
        notes = (qa.notes or "") + "；教案审核通过"
        if revise_count > 0:
            notes += f"；此前已打回修改 {revise_count} 次"
        qa = qa.model_copy(update={"revised": revise_count > 0, "notes": notes})
        report_progress("lesson_plan_review", "教案审核通过 → 进入后续生成")
        safe_log("[教案审核员] 通过 → 进入习题/课件")
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
            qa = qa.model_copy(
                update={
                    "revised": True,
                    "notes": (qa.notes or "")
                    + f"；已打回修改 {revise_count} 次仍未完全通过，继续后续流程",
                }
            )
            report_progress("lesson_plan_review", "复审仍有问题，继续后续流程")
            safe_log(f"[教案审核员] 复审仍未通过，放行后续: {qa.issues}")

    _gap()
    return {"lesson_plan_qa": qa.model_dump()}


def _route_after_lesson_review(state: PrepState) -> Literal["revise", "continue"]:
    """条件路由：不通过且未达修改上限 → 打回；否则继续。"""
    qa_raw = state.get("lesson_plan_qa") or {}
    passed = bool(qa_raw.get("passed"))
    revise_count = int(state.get("lesson_plan_revise_count") or 0)
    if (not passed) and revise_count < MAX_LESSON_PLAN_REVISES:
        return "revise"
    return "continue"


def _exercise_node(state: PrepState) -> dict:
    """习题组卷 + 对照教案质检；不通过则回修一次。"""
    report_progress("exercises", "习题组卷师工作中")
    safe_log("[习题] 习题组卷师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    paper = run_exercise_agent(lesson, plan)
    safe_log("[习题] 初稿完成，进入对照教案质检")

    report_progress("exercises", "习题对照教案质检中")
    qa = review_exercise_paper(lesson, plan, paper)
    revised = False
    if not qa.passed:
        report_progress("exercises", "习题质检未通过，回修中（仅一次）")
        safe_log(f"[习题] 质检未通过: {qa.issues} → 回修一次")
        paper = revise_exercise_paper(lesson, plan, paper, qa)
        revised = True
        qa = review_exercise_paper(lesson, plan, paper)
        qa = qa.model_copy(
            update={
                "revised": True,
                "notes": (qa.notes or "")
                + "；已回修一次"
                + ("；复检仍有问题，继续后续流程" if not qa.passed else "；复检通过"),
            }
        )
        safe_log(f"[习题] 回修完成，复检 passed={qa.passed}")
    else:
        qa = qa.model_copy(update={"revised": False, "notes": qa.notes or "质检通过，无需回修"})
        safe_log("[习题] 质检通过")

    report_progress("exercises", "习题组卷完成" + ("（已回修）" if revised else ""))
    _gap()
    return {
        "exercise_paper": paper.model_dump(),
        "exercise_qa": qa.model_dump(),
    }


def _slides_node(state: PrepState) -> dict:
    """课件生成 + 对照教案环节质检；不通过则回修一次。"""
    report_progress("slides", "课件生成师工作中（搜图/生图）")
    safe_log("[课件] 课件生成师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    slides = run_slides_agent(lesson, plan)
    safe_log("[课件] 初稿完成，进入对照环节质检")

    report_progress("slides", "课件对照环节质检中")
    qa = review_slides(lesson, plan, slides)
    revised = False
    if not qa.passed:
        report_progress("slides", "课件质检未通过，回修中（仅一次）")
        safe_log(f"[课件] 质检未通过: {qa.issues} → 回修一次")
        slides = revise_slides(lesson, plan, slides, qa)
        revised = True
        qa = review_slides(lesson, plan, slides)
        qa = qa.model_copy(
            update={
                "revised": True,
                "notes": (qa.notes or "")
                + "；已回修一次"
                + ("；复检仍有问题，继续后续流程" if not qa.passed else "；复检通过"),
            }
        )
        safe_log(f"[课件] 回修完成，复检 passed={qa.passed}")
    else:
        qa = qa.model_copy(update={"revised": False, "notes": qa.notes or "质检通过，无需回修"})
        safe_log("[课件] 质检通过")

    report_progress("slides", "课件大纲完成" + ("（已回修）" if revised else ""))
    _gap()
    return {
        "slides": slides.model_dump(),
        "slides_qa": qa.model_dump(),
    }


def _blackboard_node(state: PrepState) -> dict:
    report_progress("blackboard", "板书设计师工作中")
    safe_log("[板书] 板书设计师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    board = run_blackboard_agent(lesson, plan)
    safe_log("[板书] 板书设计完成")
    return {"blackboard": board.model_dump()}


def _run_lesson_plan_with_review(state: PrepState, on_checkpoint) -> PrepState:
    """
    课标之后的教案回路：

      教案设计师 ──→ 教案审核员
                         ├─ 通过 ──→ 继续（习题 → 课件 → …）
                         └─ 不通过 ──→ 教案设计师（修改）──→ 再审（最多打回 1 次）
    """
    state.update(_lesson_plan_design_node(state))
    if on_checkpoint:
        on_checkpoint(_state_to_result(state))

    while True:
        state.update(_lesson_plan_review_node(state))
        if on_checkpoint:
            on_checkpoint(_state_to_result(state))

        route = _route_after_lesson_review(state)
        if route == "continue":
            break

        state.update(_lesson_plan_revise_node(state))
        if on_checkpoint:
            on_checkpoint(_state_to_result(state))

    return state


def build_graph():
    """课标 → 教案设计师 ⇄ 教案审核员 → 习题 → 课件 → 板书。"""
    try:
        from langgraph.graph import END, START, StateGraph
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "未安装 langgraph。请使用项目虚拟环境："
            " .\\.venv\\Scripts\\python -m pip install -r requirements.txt"
        ) from exc

    graph = StateGraph(PrepState)
    graph.add_node("curriculum_analyst", _curriculum_node)
    graph.add_node("lesson_designer", _lesson_plan_design_node)
    graph.add_node("lesson_plan_reviewer", _lesson_plan_review_node)
    graph.add_node("lesson_reviser", _lesson_plan_revise_node)
    graph.add_node("exercise_designer", _exercise_node)
    graph.add_node("slides_designer", _slides_node)
    graph.add_node("blackboard_designer", _blackboard_node)

    graph.add_edge(START, "curriculum_analyst")
    graph.add_edge("curriculum_analyst", "lesson_designer")
    graph.add_edge("lesson_designer", "lesson_plan_reviewer")
    graph.add_conditional_edges(
        "lesson_plan_reviewer",
        _route_after_lesson_review,
        {"revise": "lesson_reviser", "continue": "exercise_designer"},
    )
    graph.add_edge("lesson_reviser", "lesson_plan_reviewer")
    graph.add_edge("exercise_designer", "slides_designer")
    graph.add_edge("slides_designer", "blackboard_designer")
    graph.add_edge("blackboard_designer", END)

    return graph.compile()


def _state_to_result(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "input": state.get("input"),
        "curriculum_analysis": state.get("curriculum_analysis"),
        "lesson_plan": state.get("lesson_plan"),
        "lesson_plan_qa": state.get("lesson_plan_qa"),
        "lesson_plan_revise_count": state.get("lesson_plan_revise_count", 0),
        "exercise_paper": state.get("exercise_paper"),
        "exercise_qa": state.get("exercise_qa"),
        "slides": state.get("slides"),
        "slides_qa": state.get("slides_qa"),
        "blackboard": state.get("blackboard"),
        "retrieved_context": state.get("retrieved_context"),
        "errors": state.get("errors", []),
    }


def _prepare_state(
    lesson_input: dict[str, Any],
    *,
    resume_from: PipelineStep | None,
    prior_state: dict[str, Any] | None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "input": lesson_input,
        "errors": [],
        "lesson_plan_revise_count": 0,
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
            "retrieved_context",
        ):
            if prior_state.get(key) is not None:
                state[key] = prior_state[key]

    if resume_from:
        if resume_from not in PIPELINE_STEPS:
            raise ValueError(f"不支持的重跑起点: {resume_from}")
        for key in _STEP_OUTPUT_KEYS[resume_from]:
            state.pop(key, None)
        if resume_from == "lesson_plan":
            state["lesson_plan_revise_count"] = 0

        need: dict[PipelineStep, list[str]] = {
            "lesson_plan": ["curriculum_analysis"],
            "exercises": ["curriculum_analysis", "lesson_plan"],
            "slides": ["curriculum_analysis", "lesson_plan"],
            "blackboard": ["curriculum_analysis", "lesson_plan"],
        }
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
) -> dict:
    """跑备课流水线；教案环节含审核员打回回路。"""
    set_progress_callback(on_progress)
    try:
        state = _prepare_state(
            lesson_input,
            resume_from=resume_from,
            prior_state=prior_state if (resume_from or prior_state) else None,
        )
        start_idx = PIPELINE_STEPS.index(resume_from) if resume_from else 0
        if resume_from:
            safe_log(f"从步骤重跑: {resume_from}（index={start_idx}）")

        for step in PIPELINE_STEPS[start_idx:]:
            try:
                if step == "lesson_plan":
                    state = _run_lesson_plan_with_review(state, on_checkpoint)
                    continue

                fn = {
                    "curriculum": _curriculum_node,
                    "exercises": _exercise_node,
                    "slides": _slides_node,
                    "blackboard": _blackboard_node,
                }[step]
                updates = fn(state)
                state.update(updates)
                if on_checkpoint:
                    on_checkpoint(_state_to_result(state))
            except Exception:
                if on_checkpoint:
                    on_checkpoint(_state_to_result(state))
                raise
        return _state_to_result(state)
    finally:
        set_progress_callback(None)
