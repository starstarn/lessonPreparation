"""质检规则、空话过滤、一致性 active_modules。"""

from __future__ import annotations

from lesson_prep.agents import (
    _finalize_pass,
    _is_non_issue,
    _rule_issues_for_consistency,
    _rule_issues_for_lesson_plan,
)
from lesson_prep.schemas import (
    Blackboard,
    BoardItem,
    ExerciseItem,
    ExercisePaper,
    SlidePage,
    Slides,
)


def test_is_non_issue_filters_empty_talk():
    assert _is_non_issue("未发现明显问题")
    assert _is_non_issue("基本一致，可以进入后续环节")
    assert _is_non_issue("")


def test_is_non_issue_keeps_real_problems():
    assert not _is_non_issue("课件 linked_stage 与教案环节不一致")
    assert not _is_non_issue("环节时长之和相差过大")
    # 肯定后转折出硬伤（需命中代码里的转折硬伤词）
    assert not _is_non_issue("未发现问题，但存在不一致")
    assert not _is_non_issue("未发现问题，但存在超纲硬伤")


def test_finalize_pass_overrides_llm_false_positive():
    passed, substantive, _notes = _finalize_pass(
        ["未发现明显问题", "可以上课"],
        notes="模型误判",
    )
    assert passed is True
    assert substantive == []


def test_finalize_pass_keeps_hard_issues():
    passed, substantive, _notes = _finalize_pass(
        ["未发现明显问题", "主板书为空"],
    )
    assert passed is False
    assert substantive == ["主板书为空"]


def test_lesson_plan_rule_flags_duration_mismatch(sample_lesson, sample_curriculum, valid_plan):
    # 把环节时长故意拉大
    bad = valid_plan.model_copy(
        update={
            "stages": [
                s.model_copy(update={"duration_minutes": 30}) for s in valid_plan.stages
            ]
        }
    )
    issues, _fixes = _rule_issues_for_lesson_plan(sample_lesson, bad, sample_curriculum)
    assert any("时长" in x for x in issues)


def test_lesson_plan_rule_flags_meta_in_objectives(
    sample_lesson, sample_curriculum, valid_plan
):
    dirty = valid_plan.model_copy(
        update={"teaching_objectives": ["理解法则；质检意见：通过"]}
    )
    issues, _fixes = _rule_issues_for_lesson_plan(sample_lesson, dirty, sample_curriculum)
    assert any("元信息" in x for x in issues)


def test_lesson_plan_rule_passes_valid(sample_lesson, sample_curriculum, valid_plan):
    issues, _fixes = _rule_issues_for_lesson_plan(
        sample_lesson, valid_plan, sample_curriculum
    )
    assert issues == []


def _make_aligned_materials(valid_plan):
    stage_names = [s.name for s in valid_plan.stages]
    paper = ExercisePaper(
        title="有理数的加法练习",
        knowledge_coverage=["同号两数相加", "异号两数相加"],
        items=[
            ExerciseItem(
                index=1,
                stem="计算 (-3)+(-5)",
                knowledge_point="同号两数相加",
                answer="-8",
            )
        ],
    )
    slides = Slides(
        pages=[
            SlidePage(index=1, title="有理数的加法", linked_stage=stage_names[0]),
            *[
                SlidePage(index=i + 2, title=n, linked_stage=n)
                for i, n in enumerate(stage_names)
            ],
        ]
    )
    board = Blackboard(
        main_board=[BoardItem(order=1, text="有理数的加法法则")],
        linked_stages=stage_names,
        key_sentences=["同号相加取同号"],
    )
    return paper, slides, board


def test_consistency_full_check_passes_when_aligned(
    sample_lesson, valid_plan
):
    paper, slides, board = _make_aligned_materials(valid_plan)
    issues, _fixes, modules = _rule_issues_for_consistency(
        sample_lesson, valid_plan, paper, slides, board
    )
    assert issues == []
    assert modules == []


def test_consistency_flags_unknown_slide_stage(sample_lesson, valid_plan):
    paper, slides, board = _make_aligned_materials(valid_plan)
    slides = slides.model_copy(
        update={
            "pages": [
                SlidePage(index=1, title="错", linked_stage="探究"),
                *slides.pages[1:],
            ]
        }
    )
    issues, _fixes, modules = _rule_issues_for_consistency(
        sample_lesson, valid_plan, paper, slides, board
    )
    assert any("linked_stage" in x or "不在教案环节" in x for x in issues)
    assert "slides" in modules


def test_consistency_active_modules_skips_disabled_materials(
    sample_lesson, valid_plan
):
    """仅启用习题时，空课件/空板书不应报错。"""
    paper = ExercisePaper(
        title="有理数的加法练习",
        knowledge_coverage=["同号两数相加", "异号两数相加"],
        items=[
            ExerciseItem(
                index=1,
                stem="计算",
                knowledge_point="同号两数相加",
                answer="1",
            )
        ],
    )
    empty_slides = Slides(pages=[])
    empty_board = Blackboard()
    issues, _fixes, modules = _rule_issues_for_consistency(
        sample_lesson,
        valid_plan,
        paper,
        empty_slides,
        empty_board,
        active_modules={"exercises"},
    )
    assert not any("课件" in x for x in issues)
    assert not any("板书" in x for x in issues)
    assert "slides" not in modules
    assert "blackboard" not in modules
