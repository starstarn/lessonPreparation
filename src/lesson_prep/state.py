from __future__ import annotations

from typing import Annotated, Any, TypedDict


class PrepState(TypedDict, total=False):
    """Shared state for the lesson-preparation multi-agent graph."""

    input: dict[str, Any]
    curriculum_analysis: dict[str, Any]
    lesson_plan: dict[str, Any]
    lesson_plan_qa: dict[str, Any]
    exercise_paper: dict[str, Any]
    slides: dict[str, Any]
    blackboard: dict[str, Any]
    retrieved_context: str
    errors: Annotated[list[str], lambda a, b: (a or []) + (b or [])]
