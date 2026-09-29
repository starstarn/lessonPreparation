"""测试公共配置：默认开启 MOCK_LLM，避免真实 API 调用。"""

from __future__ import annotations

import os

# 必须在导入 lesson_prep.* 之前设置
os.environ.setdefault("MOCK_LLM", "true")
os.environ.setdefault("PLAN_CONFIRM_GATE", "false")
os.environ.setdefault("MEDIA_SEARCH_ENABLED", "false")

import pytest


@pytest.fixture
def sample_lesson_input() -> dict:
    return {
        "stage": "初中",
        "subject": "数学",
        "textbook_version": "人教版",
        "grade": "七年级",
        "unit": "有理数",
        "lesson_title": "有理数的加法",
        "duration_minutes": 45,
        "curriculum_year": "2022",
        "extra_notes": "突出符号法则",
        "learning_profile": {
            "class_level": "average",
            "prior_knowledge": "已认识正负数与数轴",
            "known_pain_points": "异号两数相加易错",
            "focus": "key_points",
        },
        "agent_profile": "full",
        "enabled_agents": None,
    }


@pytest.fixture
def sample_lesson():
    from lesson_prep.schemas import LessonInput

    return LessonInput(
        grade="七年级",
        unit="有理数",
        lesson_title="有理数的加法",
        duration_minutes=45,
    )


@pytest.fixture
def valid_plan(sample_lesson):
    from lesson_prep.schemas import LessonPlan, LessonStage

    return LessonPlan(
        teaching_objectives=[
            "理解有理数加法法则",
            "能正确计算同号、异号两数相加",
        ],
        key_points=["同号两数相加", "异号两数相加"],
        difficult_points=["异号两数相加的符号确定"],
        materials=["数轴", "黑板"],
        stages=[
            LessonStage(
                name="导入",
                duration_minutes=5,
                teacher_activity="创设情境引入正负数",
                student_activity="举例生活中的正负数",
                purpose="激发兴趣",
            ),
            LessonStage(
                name="新授",
                duration_minutes=20,
                teacher_activity="讲解同号异号加法法则",
                student_activity="观察数轴并归纳",
                purpose="掌握法则",
            ),
            LessonStage(
                name="巩固",
                duration_minutes=15,
                teacher_activity="组织练习并点评",
                student_activity="完成随堂练习",
                purpose="巩固应用",
            ),
            LessonStage(
                name="小结",
                duration_minutes=5,
                teacher_activity="引导学生总结",
                student_activity="口述本节要点",
                purpose="梳理知识",
            ),
        ],
        practice_intents=["基础计算", "符号判断"],
        assessment_ideas=["课堂练习正确率"],
    )


@pytest.fixture
def sample_curriculum():
    from lesson_prep.schemas import CurriculumAnalysis

    return CurriculumAnalysis(
        core_competencies=["运算能力", "推理意识"],
        academic_requirements=["理解有理数加法法则"],
        # 需能与教案 key_points / objectives 弱匹配，避免规则误报「关联较弱」
        content_points=["同号两数相加", "异号两数相加", "数轴"],
        teaching_tips_from_standard=["结合数轴理解"],
        confidence="high",
    )
