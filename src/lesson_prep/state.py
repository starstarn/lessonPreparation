from __future__ import annotations

from typing import Annotated, Any, TypedDict


class PrepState(TypedDict, total=False):
    """Shared state for the lesson-preparation multi-agent graph."""

    input: dict[str, Any]
    agent_plan: dict[str, Any]
    curriculum_analysis: dict[str, Any]
    lesson_plan: dict[str, Any]
    lesson_plan_qa: dict[str, Any]
    lesson_plan_revise_count: int
    # 教案定稿后提取，课件 / 习题 / 板书生成时的硬约束
    teaching_anchors: list[dict[str, Any]]
    exercise_paper: dict[str, Any]
    exercise_qa: dict[str, Any]
    slides: dict[str, Any]
    slides_qa: dict[str, Any]
    blackboard: dict[str, Any]
    consistency_qa: dict[str, Any]
    consistency_revise_count: int
    materials_lanes: dict[str, Any]
    materials_failed: list[str]
    awaiting_plan_confirm: bool
    retrieved_context: str
    errors: Annotated[list[str], lambda a, b: (a or []) + (b or [])]
