from __future__ import annotations

import json
import re
from typing import TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from lesson_prep.config import MOCK_LLM
from lesson_prep.llm import get_chat_model
from lesson_prep.logutil import safe_log
from lesson_prep.schemas import (
    Blackboard,
    BoardItem,
    Citation,
    CurriculumAnalysis,
    LessonInput,
    LessonPlan,
    LessonStage,
    SlidePage,
    Slides,
)

T = TypeVar("T", bound=BaseModel)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _invoke_structured(system: str, user: str, schema: type[T], temperature: float = 0.2) -> T:
    """智谱兼容：用提示词强制输出 JSON，再校验为 Pydantic 模型。"""
    if MOCK_LLM:
        raise RuntimeError("MOCK 路径应由各 agent 自行处理")

    import time

    from openai import RateLimitError

    llm = get_chat_model(temperature=temperature)
    schema_hint = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    messages = [
        SystemMessage(
            content=(
                f"{system}\n\n"
                "【输出要求】只输出一个合法 JSON 对象，不要 Markdown 代码块，不要解释文字。\n"
                f"JSON 必须符合以下 Schema：\n{schema_hint}"
            )
        ),
        HumanMessage(content=user),
    ]

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            raw = llm.invoke(messages)
            content = raw.content if isinstance(raw.content, str) else str(raw.content)
            return schema.model_validate(_extract_json(content))
        except RateLimitError as exc:
            last_error = exc
            wait_s = 20 * (attempt + 1)
            safe_log(f"  触发限流，{wait_s}s 后重试 ({attempt + 1}/3)...")
            time.sleep(wait_s)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            messages.append(
                HumanMessage(
                    content=(
                        f"上次输出无法解析为 JSON（错误：{exc}）。"
                        "请重新只输出合法 JSON 对象。"
                    )
                )
            )
            time.sleep(2)
    assert last_error is not None
    raise last_error


def run_curriculum_agent(lesson: LessonInput, context: str) -> CurriculumAnalysis:
    if MOCK_LLM:
        quote = (context or "").replace("\n", " ")[:120] or "（无检索片段）"
        return CurriculumAnalysis(
            core_competencies=["抽象能力", "运算能力", "应用意识"],
            academic_requirements=[
                f"理解并掌握与「{lesson.lesson_title}」相关的核心概念与方法",
                "能解释运算结果的意义，并解决简单实际问题",
            ],
            content_points=[lesson.lesson_title, "相关概念辨析", "运算法则与应用"],
            teaching_tips_from_standard=["问题驱动", "数轴等直观模型", "联系生活情境"],
            citations=[
                Citation(
                    source="knowledge-rag",
                    page=None,
                    quote=quote,
                )
            ],
            confidence="medium",
        )

    system = (
        "你是中小学数学「课标解读员」。根据检索到的《义务教育数学课程标准》片段，"
        "提取与本课时最相关的核心素养、学业要求、内容要点与教学提示。"
        "必须基于给定片段，禁止编造课标原文；引用写入 citations。"
        "若片段不足，降低 confidence，并只写有依据的内容。"
    )
    user = (
        f"课时信息:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
        f"课标检索片段:\n{context}\n\n"
        "请输出结构化课标解读。"
    )
    return _invoke_structured(system, user, CurriculumAnalysis, temperature=0.1)


def run_lesson_plan_agent(
    lesson: LessonInput,
    curriculum: CurriculumAnalysis,
) -> LessonPlan:
    profile = lesson.learning_profile
    if MOCK_LLM:
        return LessonPlan(
            teaching_objectives=[
                f"理解{lesson.lesson_title}的基本含义",
                f"能解决与{lesson.lesson_title}相关的简单问题",
            ],
            key_points=[f"{lesson.lesson_title}的核心概念", "基本方法"],
            difficult_points=[profile.known_pain_points or "概念辨析与灵活应用"],
            materials=["黑板", "课件", "示例题卡片"],
            stages=[
                LessonStage(
                    name="导入",
                    duration_minutes=5,
                    teacher_activity="创设情境，提出核心问题",
                    student_activity="观察、猜想",
                    purpose="激发兴趣，引出课题",
                    board_hint="课题标题",
                    slide_hint="情境图",
                ),
                LessonStage(
                    name="新授",
                    duration_minutes=20,
                    teacher_activity="讲解概念与例题，组织探究",
                    student_activity="合作讨论、归纳",
                    purpose="突破重点",
                    board_hint="概念+例题",
                    slide_hint="定义与步骤",
                ),
                LessonStage(
                    name="巩固",
                    duration_minutes=12,
                    teacher_activity="组织分层练习并点评",
                    student_activity="独立练习、互评",
                    purpose="落实目标",
                    board_hint="易错提醒",
                    slide_hint="练习页",
                ),
                LessonStage(
                    name="小结",
                    duration_minutes=8,
                    teacher_activity="引导学生回顾",
                    student_activity="说收获",
                    purpose="提炼方法",
                    board_hint="方法框图",
                    slide_hint="总结页",
                ),
            ],
            practice_intents=["基础辨认", "变式应用", "表达说理"],
            assessment_ideas=["课堂提问", "练习正确率观察", "小结口述"],
            approved=True,
        )

    system = (
        "你是「教案设计师」。综合课标解读与教师输入（含可选学情卡片），"
        "生成一节完整可上课的教案。"
        "规则：\n"
        "1) 教学目标对齐课标核心素养与内容要点；\n"
        "2) 若填写了学情，必须体现在重难点与环节设计中；未填写则按课标正常设计，禁止编造班级考试数据；\n"
        "3) practice_intents 只写练习意图，不要出具体题库题目；\n"
        "4) 环节时长之和接近 duration_minutes。"
    )
    user = (
        f"教师输入:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
        f"课标解读:\n{curriculum.model_dump_json(ensure_ascii=False)}\n\n"
        "请输出结构化教案。"
    )
    return _invoke_structured(system, user, LessonPlan, temperature=0.3)


def run_slides_agent(lesson: LessonInput, plan: LessonPlan) -> Slides:
    if MOCK_LLM:
        pages = []
        for i, stage in enumerate(plan.stages, start=1):
            pages.append(
                SlidePage(
                    index=i,
                    title=f"{stage.name}：{lesson.lesson_title}",
                    bullets=[stage.purpose, stage.teacher_activity[:40]],
                    interaction=stage.student_activity,
                    visual_keywords=[lesson.lesson_title, stage.name, "示意图"],
                    media_type_suggestion="diagram",
                    linked_stage=stage.name,
                )
            )
        return Slides(pages=pages, design_notes="简洁清晰，一页一个重点，少字多图示")

    system = (
        "你是「课件生成师」。根据教案生成 PPT 大纲。"
        "每页包含标题、要点、互动提示、配图/视频检索关键词（visual_keywords）与素材类型建议。"
        "不要声称已下载真实素材，只给检索建议；风格适合课堂投影。"
    )
    user = (
        f"课题: {lesson.lesson_title}\n"
        f"学段年级: {lesson.stage} {lesson.grade}\n"
        f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        "请输出课件大纲。"
    )
    return _invoke_structured(system, user, Slides, temperature=0.3)


def run_blackboard_agent(lesson: LessonInput, plan: LessonPlan) -> Blackboard:
    if MOCK_LLM:
        main = [
            BoardItem(order=1, text=lesson.lesson_title, level=1),
            BoardItem(order=2, text="一、概念", level=1),
            BoardItem(order=3, text="定义 / 要点", level=2),
            BoardItem(order=4, text="二、例题", level=1),
            BoardItem(order=5, text="步骤：1. … 2. …", level=2),
            BoardItem(order=6, text="三、方法总结", level=1),
        ]
        return Blackboard(
            layout="main_side",
            main_board=main,
            side_board=[BoardItem(order=1, text="易错：审题 / 符号", level=1)],
            writing_sequence=["写课题", "板书概念", "板书例题步骤", "补易错", "收束方法"],
            key_sentences=[f"本节核心：{lesson.lesson_title}"],
            linked_stages=[s.name for s in plan.stages],
        )

    system = (
        "你是「板书设计师」。根据教案设计课堂板书："
        "布局、主板书条目（含书写顺序）、副板书、书写序列与关键句。"
        "板书应简洁、层次清楚，适合边讲边写。"
    )
    user = (
        f"课题: {lesson.lesson_title}\n"
        f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        "请输出板书设计。"
    )
    return _invoke_structured(system, user, Blackboard, temperature=0.3)


def dumps_pretty(model: BaseModel) -> str:
    return json.dumps(model.model_dump(), ensure_ascii=False, indent=2)
