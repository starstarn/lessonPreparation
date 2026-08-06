from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph

from lesson_prep.agents import (
    run_blackboard_agent,
    run_curriculum_agent,
    run_exercise_agent,
    run_lesson_plan_agent,
    run_slides_agent,
)
from lesson_prep.config import MOCK_LLM
from lesson_prep.logutil import safe_log
from lesson_prep.progress import report_progress, set_progress_callback
from lesson_prep.rag import retrieve_curriculum_context
from lesson_prep.schemas import CurriculumAnalysis, LessonInput, LessonPlan
from lesson_prep.state import PrepState


def _gap() -> None:
    if not MOCK_LLM:
        time.sleep(2)


def _curriculum_node(state: PrepState) -> dict:
    report_progress("curriculum", "课标解读员工作中")
    safe_log("[1/5] 课标解读员 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    query = (
        f"{lesson.stage}{lesson.grade}{lesson.subject} {lesson.unit} {lesson.lesson_title} "
        f"核心素养 学业要求 内容要求 教学提示"
    )
    context, _docs = retrieve_curriculum_context(query)
    analysis = run_curriculum_agent(lesson, context)
    safe_log("[1/5] 课标解读完成")
    _gap()
    return {
        "retrieved_context": context,
        "curriculum_analysis": analysis.model_dump(),
    }


def _lesson_plan_node(state: PrepState) -> dict:
    report_progress("lesson_plan", "教案设计师工作中")
    safe_log("[2/5] 教案设计师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    curriculum = CurriculumAnalysis.model_validate(state["curriculum_analysis"])
    plan = run_lesson_plan_agent(lesson, curriculum)
    safe_log("[2/5] 教案生成完成")
    _gap()
    return {"lesson_plan": plan.model_dump()}


def _exercise_node(state: PrepState) -> dict:
    report_progress("exercises", "习题组卷师工作中")
    safe_log("[3/5] 习题组卷师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    paper = run_exercise_agent(lesson, plan)
    safe_log("[3/5] 习题组卷完成")
    _gap()
    return {"exercise_paper": paper.model_dump()}


def _slides_node(state: PrepState) -> dict:
    report_progress("slides", "课件生成师工作中")
    safe_log("[4/5] 课件生成师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    slides = run_slides_agent(lesson, plan)
    safe_log("[4/5] 课件大纲完成")
    _gap()
    return {"slides": slides.model_dump()}


def _blackboard_node(state: PrepState) -> dict:
    report_progress("blackboard", "板书设计师工作中")
    safe_log("[5/5] 板书设计师 工作中...")
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    board = run_blackboard_agent(lesson, plan)
    safe_log("[5/5] 板书设计完成")
    return {"blackboard": board.model_dump()}


def build_graph():
    """课标解读 → 教案 → 习题组卷 → 课件 → 板书（串行，降低限流）。"""
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


def run_preparation(lesson_input: dict, on_progress=None) -> dict:
    app = build_graph()
    set_progress_callback(on_progress)
    try:
        final_state = app.invoke({"input": lesson_input, "errors": []})
    finally:
        set_progress_callback(None)
    return {
        "input": final_state.get("input"),
        "curriculum_analysis": final_state.get("curriculum_analysis"),
        "lesson_plan": final_state.get("lesson_plan"),
        "exercise_paper": final_state.get("exercise_paper"),
        "slides": final_state.get("slides"),
        "blackboard": final_state.get("blackboard"),
        "retrieved_context": final_state.get("retrieved_context"),
        "errors": final_state.get("errors", []),
    }
