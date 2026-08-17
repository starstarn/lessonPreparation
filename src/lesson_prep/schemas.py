from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LearningProfile(BaseModel):
    class_level: Literal["weak", "average", "strong"] = "average"
    prior_knowledge: str = ""
    known_pain_points: str = ""
    focus: Literal["foundation", "key_points", "extension"] = "key_points"


class LessonInput(BaseModel):
    stage: str = "初中"
    subject: str = "数学"
    textbook_version: str = "人教版"
    grade: str = "七年级"
    unit: str = ""
    lesson_title: str
    duration_minutes: int = 45
    curriculum_year: str = "2022"
    extra_notes: str = ""
    learning_profile: LearningProfile = Field(default_factory=LearningProfile)


class Citation(BaseModel):
    source: str
    page: int | None = None
    quote: str


class CurriculumAnalysis(BaseModel):
    core_competencies: list[str] = Field(default_factory=list)
    academic_requirements: list[str] = Field(default_factory=list)
    content_points: list[str] = Field(default_factory=list)
    teaching_tips_from_standard: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


class LessonStage(BaseModel):
    name: str
    duration_minutes: int
    teacher_activity: str
    student_activity: str
    purpose: str
    board_hint: str = ""
    slide_hint: str = ""


class LessonPlan(BaseModel):
    teaching_objectives: list[str] = Field(default_factory=list, min_length=1)
    key_points: list[str] = Field(default_factory=list, min_length=1)
    difficult_points: list[str] = Field(default_factory=list, min_length=1)
    materials: list[str] = Field(default_factory=list)
    stages: list[LessonStage] = Field(default_factory=list, min_length=1)
    practice_intents: list[str] = Field(default_factory=list)
    assessment_ideas: list[str] = Field(default_factory=list)
    approved: bool = True


class LessonPlanQAReport(BaseModel):
    """质检结果（教案/习题等共用结构）；不通过时可触发一次回修。"""

    passed: bool = True
    issues: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    revised: bool = False
    notes: str = ""


# 与教案质检同结构，便于前端统一展示
ExercisePaperQAReport = LessonPlanQAReport
SlidesQAReport = LessonPlanQAReport


class ConsistencyReport(BaseModel):
    """一致性检查：习题 / 课件 / 板书是否对齐同一教案。"""

    passed: bool = True
    issues: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    conflict_modules: list[Literal["exercises", "slides", "blackboard"]] = Field(
        default_factory=list
    )
    revised: bool = False
    notes: str = ""


class SlidePage(BaseModel):
    index: int
    title: str
    bullets: list[str] = Field(default_factory=list)
    interaction: str = ""
    visual_keywords: list[str] = Field(default_factory=list)
    media_type_suggestion: Literal["image", "video", "diagram", "none"] = "diagram"
    linked_stage: str = ""
    image_id: str = ""
    image_source: Literal["search", "generated", "placeholder", "none"] = "none"
    image_caption: str = ""


class Slides(BaseModel):
    pages: list[SlidePage] = Field(default_factory=list)
    design_notes: str = ""


class BoardItem(BaseModel):
    order: int
    text: str = ""
    level: int = 1


class Blackboard(BaseModel):
    layout: Literal["main_side", "timeline", "tree", "compare"] = "main_side"
    main_board: list[BoardItem] = Field(default_factory=list)
    side_board: list[BoardItem] = Field(default_factory=list)
    writing_sequence: list[str] = Field(default_factory=list)
    key_sentences: list[str] = Field(default_factory=list)
    linked_stages: list[str] = Field(default_factory=list)


class ExerciseItem(BaseModel):
    index: int
    question_type: Literal["choice", "fill", "short", "calculation", "application"] = "calculation"
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    knowledge_point: str = ""
    stem: str
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    analysis: str = ""
    score: int = 5
    source: Literal["bank", "generated"] = "generated"
    source_id: str = ""


class DifficultyDistribution(BaseModel):
    easy: int = 0
    medium: int = 0
    hard: int = 0


class ExercisePaper(BaseModel):
    """随堂/课后习题卷：优先题库选题，不足处由模型补生成。"""

    title: str = ""
    total_score: int = 100
    time_limit_minutes: int = 20
    difficulty_distribution: DifficultyDistribution = Field(default_factory=DifficultyDistribution)
    knowledge_coverage: list[str] = Field(default_factory=list)
    items: list[ExerciseItem] = Field(default_factory=list, min_length=1)
    design_notes: str = ""

