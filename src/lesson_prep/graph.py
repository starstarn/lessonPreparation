from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from lesson_prep.agents import (
    run_blackboard_agent,
    run_curriculum_agent,
    run_lesson_plan_agent,
    run_slides_agent,
)
from lesson_prep.rag import retrieve_curriculum_context
from lesson_prep.schemas import CurriculumAnalysis, LessonInput, LessonPlan
from lesson_prep.state import PrepState


def _curriculum_node(state: PrepState) -> dict:
    print("[1/4] 课标解读员 工作中...", flush=True)
    lesson = LessonInput.model_validate(state["input"])
    query = (
        f"{lesson.stage}{lesson.grade}{lesson.subject} {lesson.unit} {lesson.lesson_title} "
        f"核心素养 学业要求 内容要求 教学提示"
    )
    context, _docs = retrieve_curriculum_context(query)
    analysis = run_curriculum_agent(lesson, context)
    print("[1/4] 课标解读完成", flush=True)
    return {
        "retrieved_context": context,
        "curriculum_analysis": analysis.model_dump(),
    }


def _lesson_plan_node(state: PrepState) -> dict:
    print("[2/4] 教案设计师 工作中...", flush=True)
    lesson = LessonInput.model_validate(state["input"])
    curriculum = CurriculumAnalysis.model_validate(state["curriculum_analysis"])
    plan = run_lesson_plan_agent(lesson, curriculum)
    print("[2/4] 教案生成完成", flush=True)
    return {"lesson_plan": plan.model_dump()}


def _slides_node(state: PrepState) -> dict:
    print("[3/4] 课件生成师 工作中...", flush=True)
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    slides = run_slides_agent(lesson, plan)
    print("[3/4] 课件大纲完成", flush=True)
    return {"slides": slides.model_dump()}


def _blackboard_node(state: PrepState) -> dict:
    print("[4/4] 板书设计师 工作中...", flush=True)
    lesson = LessonInput.model_validate(state["input"])
    plan = LessonPlan.model_validate(state["lesson_plan"])
    board = run_blackboard_agent(lesson, plan)
    print("[4/4] 板书设计完成", flush=True)
    return {"blackboard": board.model_dump()}


def build_graph():
    """
    课标解读 -> 教案设计 -> (课件 || 板书)

    说明：CLI MVP 默认教案自动 approved；后续可在教案节点后加 interrupt 人工确认。
    """
    graph = StateGraph(PrepState)
    graph.add_node("curriculum_analyst", _curriculum_node)
    graph.add_node("lesson_designer", _lesson_plan_node)
    graph.add_node("slides_designer", _slides_node)
    graph.add_node("blackboard_designer", _blackboard_node)

    graph.add_edge(START, "curriculum_analyst")
    graph.add_edge("curriculum_analyst", "lesson_designer")
    graph.add_edge("lesson_designer", "slides_designer")
    graph.add_edge("lesson_designer", "blackboard_designer")
    graph.add_edge("slides_designer", END)
    graph.add_edge("blackboard_designer", END)

    return graph.compile()


def run_preparation(lesson_input: dict) -> dict:
    app = build_graph()
    final_state = app.invoke({"input": lesson_input, "errors": []})
    return {
        "input": final_state.get("input"),
        "curriculum_analysis": final_state.get("curriculum_analysis"),
        "lesson_plan": final_state.get("lesson_plan"),
        "slides": final_state.get("slides"),
        "blackboard": final_state.get("blackboard"),
        "retrieved_context": final_state.get("retrieved_context"),
        "errors": final_state.get("errors", []),
    }
