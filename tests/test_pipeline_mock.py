"""MOCK 模式下的流水线装配与产物 Schema 一致性。"""

from __future__ import annotations

import os

import pytest

# conftest 已设 MOCK；再确认一次，防止被外部环境覆盖后导入错配置
assert os.environ.get("MOCK_LLM", "").lower() in {"1", "true", "yes"}


@pytest.fixture
def mock_full_result(sample_lesson_input):
    from lesson_prep.graph import run_preparation

    payload = dict(sample_lesson_input)
    payload["agent_profile"] = "full"
    # 关闭闸门，一次跑完
    return run_preparation(payload, pause_after_plan=False)


def test_mock_full_pipeline_produces_core_artifacts(mock_full_result):
    from lesson_prep.schemas import (
        Blackboard,
        ConsistencyReport,
        CurriculumAnalysis,
        ExercisePaper,
        LessonPlan,
        Slides,
    )

    r = mock_full_result
    assert r.get("agent_plan", {}).get("profile_id") == "full"
    CurriculumAnalysis.model_validate(r["curriculum_analysis"])
    LessonPlan.model_validate(r["lesson_plan"])
    ExercisePaper.model_validate(r["exercise_paper"])
    Slides.model_validate(r["slides"])
    Blackboard.model_validate(r["blackboard"])
    ConsistencyReport.model_validate(r["consistency_qa"])


def test_mock_plan_only_skips_materials(sample_lesson_input):
    from lesson_prep.graph import run_preparation

    payload = dict(sample_lesson_input)
    payload["agent_profile"] = "plan_only"
    result = run_preparation(payload, pause_after_plan=False)

    assert result["lesson_plan"] is not None
    assert result.get("slides") is None
    assert result.get("exercise_paper") is None
    assert result.get("blackboard") is None
    assert result["agent_plan"]["run_materials"] is False


def test_mock_homework_only_exercises(sample_lesson_input):
    from lesson_prep.graph import run_preparation

    payload = dict(sample_lesson_input)
    payload["agent_profile"] = "homework"
    result = run_preparation(payload, pause_after_plan=False)

    assert result.get("exercise_paper") is not None
    assert result.get("slides") is None
    assert result.get("blackboard") is None
    assert result["agent_plan"]["material_agents"] == ["exercises"]
    assert result.get("consistency_qa") is not None


def test_mock_custom_enabled_agents(sample_lesson_input):
    from lesson_prep.graph import run_preparation

    payload = dict(sample_lesson_input)
    payload["agent_profile"] = "custom"
    payload["enabled_agents"] = ["curriculum", "lesson_plan", "slides"]
    result = run_preparation(payload, pause_after_plan=False)

    assert result.get("slides") is not None
    assert result.get("exercise_paper") is None
    assert result.get("blackboard") is None
