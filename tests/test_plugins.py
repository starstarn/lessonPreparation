"""Agent 插件注册表与场景装配。"""

from __future__ import annotations

from lesson_prep.plugins import (
    build_run_plan,
    ensure_dependencies,
    list_plugins,
    list_profiles,
    resolve_enabled_agents,
)


def test_list_plugins_covers_seven_agents():
    ids = {p.id for p in list_plugins()}
    assert {
        "curriculum",
        "lesson_plan",
        "lesson_review",
        "exercises",
        "slides",
        "blackboard",
        "consistency",
    }.issubset(ids)


def test_material_plugins_declare_read_anchors_skill():
    from lesson_prep.plugins import get_plugin

    for aid in ("exercises", "slides", "blackboard"):
        assert "shared.read_anchors" in get_plugin(aid).skills
    assert "consistency.anchors" in get_plugin("consistency").skills


def test_ensure_dependencies_fills_upstream_for_exercises():
    agents = ensure_dependencies(["exercises"])
    assert "curriculum" in agents
    assert "lesson_plan" in agents
    assert "exercises" in agents
    # 拓扑顺序：课标在教案前，教案在习题前
    assert agents.index("curriculum") < agents.index("lesson_plan")
    assert agents.index("lesson_plan") < agents.index("exercises")


def test_ensure_dependencies_drops_consistency_without_materials():
    agents = ensure_dependencies(["curriculum", "lesson_plan", "consistency"])
    assert "consistency" not in agents


def test_ensure_dependencies_drops_unknown_ids():
    agents = ensure_dependencies(["exercises", "not_exist"])
    assert "not_exist" not in agents
    assert "exercises" in agents


def test_build_run_plan_plan_only():
    plan = build_run_plan(profile_id="plan_only")
    assert plan.run_curriculum
    assert plan.run_lesson_plan
    assert plan.run_lesson_review
    assert not plan.run_materials
    assert not plan.run_consistency
    assert plan.material_agents == []


def test_build_run_plan_homework():
    plan = build_run_plan(profile_id="homework")
    assert plan.material_agents == ["exercises"]
    assert plan.run_consistency


def test_custom_enabled_agents_override_profile():
    agents = resolve_enabled_agents(
        profile_id="full",
        enabled_agents=["slides", "blackboard"],
    )
    assert "slides" in agents
    assert "blackboard" in agents
    assert "exercises" not in agents
    assert "lesson_plan" in agents  # 依赖补齐


def test_profiles_include_builtin_and_file():
    ids = {p.id for p in list_profiles()}
    assert "full" in ids
    assert "plan_only" in ids
    assert "quick_draft" in ids  # data/agent_profiles.json
    assert "custom" in ids
