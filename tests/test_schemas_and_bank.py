"""Schema、题库检索等基础设施测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lesson_prep.question_bank import search_question_bank
from lesson_prep.schemas import LessonInput, LessonPlan, LessonStage


def test_lesson_input_accepts_agent_profile_fields():
    lesson = LessonInput(
        lesson_title="有理数的加法",
        agent_profile="homework",
        enabled_agents=["exercises"],
    )
    assert lesson.agent_profile == "homework"
    assert lesson.enabled_agents == ["exercises"]


def test_lesson_plan_rejects_empty_stages():
    with pytest.raises(ValidationError):
        LessonPlan(
            teaching_objectives=["目标"],
            key_points=["重点"],
            difficult_points=["难点"],
            stages=[],
        )


def test_lesson_plan_requires_stage_fields():
    plan = LessonPlan(
        teaching_objectives=["理解法则"],
        key_points=["同号加法"],
        difficult_points=["异号加法"],
        stages=[
            LessonStage(
                name="新授",
                duration_minutes=20,
                teacher_activity="讲解",
                student_activity="练习",
                purpose="掌握",
            )
        ],
    )
    assert plan.stages[0].name == "新授"


def test_question_bank_search_returns_hits_for_rational_addition():
    hits = search_question_bank("有理数的加法", grade="七年级", k=5)
    assert isinstance(hits, list)
    # demo 题库应能命中有理数相关题
    assert len(hits) >= 1
    assert all("id" in h and "stem" in h for h in hits)


def test_question_bank_search_respects_k():
    hits = search_question_bank("有理数", k=2)
    assert len(hits) <= 2
