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
from lesson_prep.schemas import CurriculumAnalysis, LessonInput, LessonPlan
from lesson_prep.state import PrepState

PipelineStep = Literal["curriculum", "lesson_plan", "exercises", "slides", "blackboard"]

PIPELINE_STEPS: list[PipelineStep] = [
    "curriculum",
    "lesson_plan",
    "exercises",
    "slides",
    "blackboard",
]

# 从某步重跑时，需要清掉该步及之后的产物
_STEP_OUTPUT_KEYS: dict[PipelineStep, list[str]] = {
    "curriculum": [
        "curriculum_analysis",
        "retrieved_context",
        "lesson_plan",
        "lesson_plan_qa",
        "exercise_paper",
        "exercise_qa",
        "slides",
        "slides_qa",
        "blackboard",
    ],
    "lesson_plan": [
        "lesson_plan",
        "lesson_plan_qa",
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
    safe_log("[1/5] 课标解读员 工作中（Tool: search_curriculum）...")
    lesson = LessonInput.model_validate(state["input"])
    analysis, context = run_curriculum_agent(lesson)
    safe_log("[1/5] 课标解读完成")
    _gap()
    return {
        "retrieved_context": context,
        "curriculum_analysis": analysis.model_dump(),
    }


def _lesson_plan_node(state: PrepState) -> dict:
    """教案生成 + 质检；不通过则回修一次，再复检（不再二次回修）。"""
    report_progress("lesson_plan", "教案设计师工作中")
    safe_log("[2/5] 教案设计师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    curriculum = CurriculumAnalysis.model_validate(state["curriculum_analysis"])
    plan = run_lesson_plan_agent(lesson, curriculum)
    safe_log("[2/5] 教案初稿完成，进入质检")

    report_progress("lesson_plan", "教案质检中")
    qa = review_lesson_plan(lesson, plan, curriculum)
    revised = False
    if not qa.passed:
        report_progress("lesson_plan", "教案质检未通过，回修中（仅一次）")
        safe_log(f"[2/5] 教案质检未通过: {qa.issues} → 回修一次")
        plan = revise_lesson_plan(lesson, plan, curriculum, qa)
        revised = True
        qa = review_lesson_plan(lesson, plan, curriculum)
        qa = qa.model_copy(
            update={
                "revised": True,
                "notes": (qa.notes or "")
                + ("；已回修一次" if revised else "")
                + ("；复检仍有问题，继续后续流程" if not qa.passed else "；复检通过"),
            }
        )
        safe_log(f"[2/5] 教案回修完成，复检 passed={qa.passed}")
    else:
        qa = qa.model_copy(update={"revised": False, "notes": qa.notes or "质检通过，无需回修"})
        safe_log("[2/5] 教案质检通过")

    report_progress("lesson_plan", "教案完成" + ("（已回修）" if revised else ""))
    _gap()
    return {
        "lesson_plan": plan.model_dump(),
        "lesson_plan_qa": qa.model_dump(),
    }


def _exercise_node(state: PrepState) -> dict:
    """习题组卷 + 对照教案质检；不通过则回修一次。"""
    report_progress("exercises", "习题组卷师工作中")
    safe_log("[3/5] 习题组卷师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    paper = run_exercise_agent(lesson, plan)
    safe_log("[3/5] 习题初稿完成，进入对照教案质检")

    report_progress("exercises", "习题对照教案质检中")
    qa = review_exercise_paper(lesson, plan, paper)
    revised = False
    if not qa.passed:
        report_progress("exercises", "习题质检未通过，回修中（仅一次）")
        safe_log(f"[3/5] 习题质检未通过: {qa.issues} → 回修一次")
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
        safe_log(f"[3/5] 习题回修完成，复检 passed={qa.passed}")
    else:
        qa = qa.model_copy(update={"revised": False, "notes": qa.notes or "质检通过，无需回修"})
        safe_log("[3/5] 习题质检通过")

    report_progress("exercises", "习题组卷完成" + ("（已回修）" if revised else ""))
    _gap()
    return {
        "exercise_paper": paper.model_dump(),
        "exercise_qa": qa.model_dump(),
    }


def _slides_node(state: PrepState) -> dict:
    """课件生成 + 对照教案环节质检；不通过则回修一次。"""
    report_progress("slides", "课件生成师工作中（搜图/生图）")
    safe_log("[4/5] 课件生成师 工作中（Tools: search_images, generate_diagram）...")
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    slides = run_slides_agent(lesson, plan)
    safe_log("[4/5] 课件初稿完成，进入对照环节质检")

    report_progress("slides", "课件对照环节质检中")
    qa = review_slides(lesson, plan, slides)
    revised = False
    if not qa.passed:
        report_progress("slides", "课件质检未通过，回修中（仅一次）")
        safe_log(f"[4/5] 课件质检未通过: {qa.issues} → 回修一次")
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
        safe_log(f"[4/5] 课件回修完成，复检 passed={qa.passed}")
    else:
        qa = qa.model_copy(update={"revised": False, "notes": qa.notes or "质检通过，无需回修"})
        safe_log("[4/5] 课件质检通过")

    report_progress("slides", "课件大纲完成" + ("（已回修）" if revised else ""))
    _gap()
    return {
        "slides": slides.model_dump(),
        "slides_qa": qa.model_dump(),
    }


def _blackboard_node(state: PrepState) -> dict:
    report_progress("blackboard", "板书设计师工作中")
    safe_log("[5/5] 板书设计师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    board = run_blackboard_agent(lesson, plan)
    safe_log("[5/5] 板书设计完成")
    return {"blackboard": board.model_dump()}


_NODE_FNS: dict[PipelineStep, Callable[[PrepState], dict]] = {
    "curriculum": _curriculum_node,
    "lesson_plan": _lesson_plan_node,
    "exercises": _exercise_node,
    "slides": _slides_node,
    "blackboard": _blackboard_node,
}


def build_graph():
    """课标解读 → 教案(+质检回修) → 习题组卷 → 课件 → 板书。

    运行时流水线已改为顺序执行（见 run_preparation）；本函数保留给需要
    LangGraph 图对象的调用方，因此按需导入 langgraph。
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "未安装 langgraph。请使用项目虚拟环境："
            " .\\.venv\\Scripts\\python -m pip install -r requirements.txt"
        ) from exc

    graph = StateGraph(PrepState)
    graph.add_node("curriculum_analyst", _curriculum_node)
    graph.add_node("lesson_designer", _lesson_plan_node)
    graph.add_node("exercise_designer", _exercise_node)
    graph.add_node("slides_designer", _slides_node)
    graph.add_node("blackboard_designer", _blackboard_node)

    graph.add_edge(START, "curriculum_analyst")
    graph.add_edge("curriculum_analyst", "lesson_designer")
    graph.add_edge("lesson_designer", "exercise_designer")
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
    state: dict[str, Any] = {"input": lesson_input, "errors": []}
    if prior_state:
        for key in (
            "curriculum_analysis",
            "lesson_plan",
            "lesson_plan_qa",
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

        # 从中后段重跑时，前置产物必须齐全
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
    """跑备课流水线；支持从指定步骤重跑，并在每步后写检查点。"""
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
                updates = _NODE_FNS[step](state)  # type: ignore[arg-type]
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
