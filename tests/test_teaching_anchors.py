"""教案三元组与 Skill：shared.read_anchors / consistency.anchors。"""

from __future__ import annotations

from lesson_prep.plugins import get_plugin
from lesson_prep.schemas import (
    Blackboard,
    BoardItem,
    ExerciseItem,
    ExercisePaper,
    LearningProfile,
    LessonInput,
    LessonPlan,
    LessonStage,
    SlidePage,
    Slides,
    TeachingAnchor,
)
from lesson_prep.skills import (
    apply_read_anchors,
    check_anchors_consistency,
    extract_teaching_anchors,
    format_anchor_constraint,
    list_skills,
    skills_for_agent,
)


def _plan() -> LessonPlan:
    return LessonPlan(
        teaching_objectives=["理解有理数加法的含义", "能解决异号相加的简单问题"],
        key_points=["有理数加法的核心概念", "基本方法"],
        difficult_points=["异号两数相加易错"],
        stages=[
            LessonStage(
                name="新授",
                duration_minutes=20,
                teacher_activity="讲解",
                student_activity="练习",
                purpose="突破重点",
            )
        ],
    )


def test_material_agents_mount_read_anchors_skill():
    for aid in ("exercises", "slides", "blackboard"):
        plugin = get_plugin(aid)
        assert plugin is not None
        assert "shared.read_anchors" in plugin.skills
    assert "consistency.anchors" in (get_plugin("consistency").skills or ())


def test_skills_registry_lists_both():
    ids = {s.id for s in list_skills()}
    assert "shared.read_anchors" in ids
    assert "consistency.anchors" in ids
    assert any(s.id == "shared.read_anchors" for s in skills_for_agent("exercises"))


def test_extract_pairs_objective_knowledge_and_difficulty():
    lesson = LessonInput(
        lesson_title="有理数的加法",
        learning_profile=LearningProfile(focus="key_points"),
    )
    anchors = extract_teaching_anchors(_plan(), lesson)
    assert len(anchors) == 3
    assert anchors[0].knowledge_point == "有理数加法的核心概念"
    assert anchors[0].difficulty == "basic"
    assert anchors[1].knowledge_point == "基本方法"
    assert anchors[1].difficulty == "intermediate"
    assert anchors[2].knowledge_point == "异号两数相加易错"
    assert anchors[2].difficulty == "advanced"
    assert all(a.objective for a in anchors)


def test_foundation_focus_keeps_regular_points_basic():
    lesson = LessonInput(
        lesson_title="有理数的加法",
        learning_profile=LearningProfile(focus="foundation"),
    )
    anchors = extract_teaching_anchors(_plan(), lesson)
    by_point = {a.knowledge_point: a.difficulty for a in anchors}
    assert by_point["有理数加法的核心概念"] == "basic"
    assert by_point["基本方法"] == "basic"
    assert by_point["异号两数相加易错"] == "intermediate"


def test_apply_read_anchors_injects_skill_marker():
    anchors = [
        TeachingAnchor(objective="理解概念", knowledge_point="相反数", difficulty="basic"),
    ]
    system, user = apply_read_anchors("你是习题组卷师。", "请出题。", anchors)
    assert "shared.read_anchors" in system
    assert "shared.read_anchors" in user
    assert "相反数" in user
    assert "请出题。" in user


def test_constraint_text_lists_every_anchor():
    anchors = [
        TeachingAnchor(objective="理解概念", knowledge_point="相反数", difficulty="basic"),
        TeachingAnchor(objective="综合运用", knowledge_point="数轴", difficulty="advanced"),
    ]
    text = format_anchor_constraint(anchors)
    assert "硬约束" in text
    assert "相反数" in text
    assert "数轴" in text
    assert "基础" in text
    assert "拓展" in text


def test_consistency_anchors_flags_missing_knowledge():
    anchors = [
        TeachingAnchor(
            objective="理解加法",
            knowledge_point="异号两数相加",
            difficulty="advanced",
        )
    ]
    paper = ExercisePaper(
        title="练习",
        knowledge_coverage=["同号相加"],
        items=[
            ExerciseItem(index=1, stem="计算 3+5", difficulty="easy", knowledge_point="同号"),
            ExerciseItem(index=2, stem="计算 1+2", difficulty="easy", knowledge_point="同号"),
            ExerciseItem(index=3, stem="计算 4+4", difficulty="easy", knowledge_point="同号"),
            ExerciseItem(index=4, stem="计算 2+2", difficulty="medium", knowledge_point="同号"),
        ],
    )
    slides = Slides(pages=[SlidePage(index=1, title="导入", bullets=["情境"])])
    board = Blackboard(
        main_board=[BoardItem(order=1, text="法则")],
        key_sentences=["同号相加"],
    )
    issues, _fixes, modules = check_anchors_consistency(
        anchors, paper, slides, board, active_modules={"exercises", "slides", "blackboard"}
    )
    assert any("异号两数相加" in x for x in issues)
    assert "exercises" in modules


def test_pipeline_stores_anchors_and_materials_read_them(sample_lesson_input):
    from lesson_prep.graph import run_preparation

    payload = dict(sample_lesson_input)
    payload["agent_profile"] = "full"
    result = run_preparation(payload, pause_after_plan=False)

    anchors = result.get("teaching_anchors") or []
    assert len(anchors) >= 2
    points = []
    for item in anchors:
        TeachingAnchor.model_validate(item)
        assert item["objective"]
        assert item["knowledge_point"]
        assert item["difficulty"] in {"basic", "intermediate", "advanced"}
        points.append(item["knowledge_point"])

    assert "三元组" in (result["exercise_paper"].get("design_notes") or "")
    assert "三元组" in (result["slides"].get("design_notes") or "")
    sentences = " ".join(result["blackboard"].get("key_sentences") or [])
    assert any(point in sentences for point in points)


def test_plan_only_still_extracts_anchors(sample_lesson_input):
    from lesson_prep.graph import run_preparation

    payload = dict(sample_lesson_input)
    payload["agent_profile"] = "plan_only"
    result = run_preparation(payload, pause_after_plan=False)
    assert result.get("teaching_anchors")
    assert result.get("slides") is None
