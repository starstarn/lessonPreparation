from __future__ import annotations

import json
import re
from typing import TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from lesson_prep.config import MOCK_LLM
from lesson_prep.llm import invoke_with_fallback
from lesson_prep.logutil import safe_log
from lesson_prep.schemas import (
    Blackboard,
    BoardItem,
    Citation,
    ConsistencyReport,
    CurriculumAnalysis,
    DifficultyDistribution,
    ExerciseItem,
    ExercisePaper,
    LessonInput,
    LessonPlan,
    LessonPlanQAReport,
    LessonStage,
    SlidePage,
    Slides,
    TeachingAnchor,
)
from lesson_prep.media_assets import attach_media_to_slides, parse_media_manifest
from lesson_prep.question_bank import search_question_bank as qb_search
from lesson_prep.skills import (
    BANK_DIFFICULTY,
    apply_read_anchors,
    check_anchors_consistency,
    extract_teaching_anchors,
    format_anchor_constraint,
    resolve_teaching_anchors,
)
from lesson_prep.tools import (
    generate_diagram,
    search_curriculum,
    search_images,
    search_question_bank,
)

T = TypeVar("T", bound=BaseModel)

# 兼容旧导入名
_BANK_DIFFICULTY = BANK_DIFFICULTY
_apply_anchor_constraint = apply_read_anchors

# 过程元信息：不应出现在教学目标 / 环节目的等可上课正文里
_META_CONTENT_MARKERS = (
    "质检意见",
    "质检",
    "审核意见",
    "进入后续组卷",
    "后续组卷",
    "回应质检",
    "确认无明显问题",
    "打回修改",
    "复审",
)

# LLM 常把「未发现问题」写进 issues，却又把 passed 设为 false
_NON_ISSUE_PREFIXES = (
    "未发现",
    "没有发现",
    "无明显",
    "未检出",
    "暂未发现",
    "基本一致",
    "整体一致",
    "可以进入",
    "可以上课",
)

_CONTRADICTION_HINTS = (
    "但是",
    "但存在",
    "但包含",
    "硬伤",
    "超出",
    "不一致",
    "矛盾",
    "冲突",
    "偏离",
    "未对齐",
    "缺少",
    "为空",
)


def _is_non_issue(text: str) -> bool:
    """肯定性空话 / 「未发现问题」类描述，不当作硬伤。"""
    t = (text or "").strip()
    if not t:
        return True
    starts_soft = any(t.startswith(p) or p in t[:12] for p in _NON_ISSUE_PREFIXES)
    if not starts_soft:
        return False
    # 「未发现 A，但存在 B」仍算问题
    if any(h in t for h in _CONTRADICTION_HINTS) and not t.startswith("未发现"):
        return False
    if t.startswith("未发现") or t.startswith("没有发现") or t.startswith("无明显"):
        # 整句在肯定无问题，除非后半明确转折出硬伤
        if "但" in t and any(
            h in t for h in ("超出", "硬伤", "矛盾", "不一致", "冲突", "偏离", "缺少")
        ):
            return False
        return True
    return True


def _filter_substantive_issues(issues: list[str]) -> list[str]:
    return [i for i in issues if i and not _is_non_issue(i)]


def _text_has_meta(text: str) -> bool:
    t = text or ""
    return any(m in t for m in _META_CONTENT_MARKERS)


def _finalize_pass(issues: list[str], *, notes: str = "") -> tuple[bool, list[str], str]:
    substantive = _filter_substantive_issues(issues)
    passed = len(substantive) == 0
    if passed and not notes:
        notes = "审核通过（无实质硬伤）"
    return passed, substantive, notes


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                text = getattr(item, "text", None)
                parts.append(str(text if text is not None else item))
        return "\n".join(p for p in parts if p)
    return str(content)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("模型未返回 JSON 对象")
    return data


def _reject_empty_plan(model: BaseModel) -> None:
    """教案关键字段为空时视为失败，触发重试。"""
    if not isinstance(model, LessonPlan):
        return
    if not model.teaching_objectives or not model.stages:
        raise ValueError(
            "教案内容为空：teaching_objectives / stages 不能为空，请重新生成完整教案 JSON"
        )


def _invoke_structured(system: str, user: str, schema: type[T], temperature: float = 0.2) -> T:
    """智谱兼容：用提示词强制输出 JSON，再校验为 Pydantic 模型。

    使用 strong 档模型；传输失败时走 llm 降级链。
    """
    if MOCK_LLM:
        raise RuntimeError("MOCK 路径应由各 agent 自行处理")

    import time

    from openai import RateLimitError

    schema_hint = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    messages = [
        SystemMessage(
            content=(
                f"{system}\n\n"
                "【输出要求】只输出一个合法 JSON 对象，不要 Markdown 代码块，不要解释文字。\n"
                "字段名必须与 Schema 完全一致（使用英文 snake_case，不要用中文字段名）。\n"
                "数组字段必须给出具体内容，禁止返回空数组 []。\n"
                f"JSON 必须符合以下 Schema：\n{schema_hint}"
            )
        ),
        HumanMessage(content=user),
    ]

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            raw = invoke_with_fallback(
                messages,
                role="strong",
                temperature=temperature,
            )
            content = _message_text(raw.content)
            model = schema.model_validate(_extract_json(content))
            _reject_empty_plan(model)
            return model
        except RateLimitError as exc:
            last_error = exc
            wait_s = 20 * (attempt + 1)
            safe_log(f"  触发限流，{wait_s}s 后重试 ({attempt + 1}/3)...")
            time.sleep(wait_s)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            safe_log(f"  结构化输出失败，重试 ({attempt + 1}/3): {exc}")
            messages.append(
                HumanMessage(
                    content=(
                        f"上次输出无法使用（错误：{exc}）。"
                        "请重新只输出合法 JSON 对象，字段名用英文，"
                        "teaching_objectives 与 stages 必须非空。"
                    )
                )
            )
            time.sleep(2)
    assert last_error is not None
    raise last_error


def _default_curriculum_queries(lesson: LessonInput) -> list[tuple[str, str]]:
    from lesson_prep.rag import suggest_curriculum_path

    specs = (
        ("核心素养", "核心素养"),
        ("学业要求 内容要求", "学业要求"),
        ("教学提示", "教学提示"),
    )
    calls: list[tuple[str, str]] = []
    for words, aspect in specs:
        query = f"{lesson.stage}{lesson.grade}{lesson.subject} {lesson.unit} {lesson.lesson_title} {words}"
        path = suggest_curriculum_path(
            grade=lesson.grade,
            stage=lesson.stage,
            unit=lesson.unit,
            lesson_title=lesson.lesson_title,
            aspect=aspect,
        )
        calls.append((query, path))
    return calls


def _run_tool_loop(
    *,
    tools: list,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_rounds: int = 4,
    first_tool_choice: str | None = None,
) -> list[str]:
    """通用 Tool Calling 循环，返回各次工具结果文本。

    使用 fast 档模型；传输失败时走 llm 降级链。
    """
    import time

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from openai import RateLimitError

    tool_map = {t.name: t for t in tools}
    messages: list = [SystemMessage(content=system), HumanMessage(content=user)]
    chunks: list[str] = []

    for round_i in range(max_rounds):
        try:
            if round_i == 0 and first_tool_choice:
                ai = invoke_with_fallback(
                    messages,
                    role="fast",
                    temperature=temperature,
                    tools=tools,
                    tool_choice=first_tool_choice,
                )
            else:
                ai = invoke_with_fallback(
                    messages,
                    role="fast",
                    temperature=temperature,
                    tools=tools,
                )
        except RateLimitError:
            wait_s = 8 * (round_i + 1)
            safe_log(f"  工具调用触发限流，{wait_s}s 后重试...")
            time.sleep(wait_s)
            continue
        except Exception as exc:  # noqa: BLE001
            if round_i == 0 and first_tool_choice:
                safe_log(f"  tool_choice 不可用，回退 auto: {exc}")
                try:
                    ai = invoke_with_fallback(
                        messages,
                        role="fast",
                        temperature=temperature,
                        tools=tools,
                    )
                except Exception as exc2:  # noqa: BLE001
                    safe_log(f"  工具调用失败: {exc2}")
                    break
            else:
                safe_log(f"  工具调用失败: {exc}")
                break

        if not isinstance(ai, AIMessage):
            break
        messages.append(ai)
        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            break

        for call in tool_calls:
            name = call.get("name") or ""
            args = call.get("args") or {}
            call_id = call.get("id") or name
            tool_fn = tool_map.get(name)
            if tool_fn is None:
                content = f"未知工具: {name}"
            else:
                try:
                    content = tool_fn.invoke(args)
                except Exception as exc:  # noqa: BLE001
                    content = f"工具执行失败: {exc}"
            if isinstance(content, str) and content.strip():
                chunks.append(content.strip())
            messages.append(ToolMessage(content=str(content), tool_call_id=call_id))
        time.sleep(1)

    return chunks


def _gather_curriculum_via_tools(lesson: LessonInput, max_rounds: int = 4) -> str:
    """让模型按需调用 search_curriculum，汇总检索片段。"""
    system = (
        "你是中小学数学「课标解读员」的检索助手。\n"
        "必须使用工具 search_curriculum 检索《义务教育数学课程标准》片段，"
        "不要凭记忆编造课标原文。\n"
        "每次调用都传 path，用「-」连接层级标签，先锁定父切片和章节，再写 query 关键词。\n"
        "示例 path：「课程目标-核心素养-数学抽象」、"
        "「内容标准-第四学段-数与代数-有理数的加法-学业要求」。\n"
        "返回里同时有子切片正文和父切片路径：正文是知识点要求，父切片用来确认整体方向。\n"
        "建议分侧面检索：核心素养、学业要求/内容要求、教学提示；"
        "若某次结果不足，可放宽 path 末级或换关键词再搜。\n"
        "检索足够后停止调用工具，简短回复「检索完成」即可。"
    )
    user = (
        f"课时信息:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
        "请先调用 search_curriculum 检索课标，再结束。"
    )
    chunks = _run_tool_loop(
        tools=[search_curriculum],
        system=system,
        user=user,
        temperature=0.1,
        max_rounds=max_rounds,
        first_tool_choice="search_curriculum",
    )

    if not chunks:
        safe_log("  未获得工具检索结果，使用默认查询兜底")
        for query, path in _default_curriculum_queries(lesson)[:2]:
            chunks.append(search_curriculum.invoke({"query": query, "path": path, "k": 4}))

    seen: set[str] = set()
    unique: list[str] = []
    for c in chunks:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return "\n\n---\n\n".join(unique)


def _gather_slide_media_via_tools(lesson: LessonInput, plan: LessonPlan, max_rounds: int = 6) -> tuple[str, list[dict[str, str]]]:
    """让课件生成师按需搜图/生图，汇总素材清单。"""
    stage_names = "、".join(s.name for s in plan.stages[:6])
    system = (
        "你是「课件生成师」的素材助手。\n"
        "根据教案为 PPT 各页准备配图。\n"
        "本轮只允许调用 search_images 搜真图（英文关键词，如 thermometer Celsius / number line math）。\n"
        "不要调用 generate_diagram。素材足够后停止。"
    )
    user = (
        f"课题: {lesson.lesson_title}\n"
        f"学段年级: {lesson.stage} {lesson.grade}\n"
        f"教学环节: {stage_names}\n"
        f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        "请调用 search_images 2～4 次，覆盖导入情境、概念示意等页面。"
    )
    chunks = _run_tool_loop(
        tools=[search_images],
        system=system,
        user=user,
        temperature=0.25,
        max_rounds=max_rounds,
        first_tool_choice="search_images",
    )

    if not chunks:
        chunks.append(
            search_images.invoke(
                {"query": f"{lesson.lesson_title} math education classroom", "limit": 1}
            )
        )

    # 真图不够时，再补 1 张本地示意图（数轴等抽象内容）
    preview_manifest = parse_media_manifest(chunks)
    real_count = sum(1 for m in preview_manifest if m.get("source") != "generated")
    if real_count < 2:
        safe_log("  真图不足，补充本地示意图")
        chunks.append(
            generate_diagram.invoke({"prompt": f"{lesson.lesson_title} 数轴 同号异号加法"})
        )

    manifest = parse_media_manifest(chunks)
    return "\n\n".join(chunks), manifest


def run_curriculum_agent(lesson: LessonInput) -> tuple[CurriculumAnalysis, str]:
    """课标解读员：按需调用 search_curriculum，再输出结构化解读。

    Returns:
        (解读结果, 汇总后的检索上下文)
    """
    if MOCK_LLM:
        context = search_curriculum.invoke(
            {
                "query": (
                    f"{lesson.stage}{lesson.grade}{lesson.subject} "
                    f"{lesson.unit} {lesson.lesson_title} 学业要求 内容要求"
                ),
                "path": "内容标准-第四学段-数与代数-有理数",
                "k": 4,
            }
        )
        quote = (context or "").replace("\n", " ")[:120] or "（无检索片段）"
        analysis = CurriculumAnalysis(
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
        return analysis, context

    context = _gather_curriculum_via_tools(lesson)
    system = (
        "你是中小学数学「课标解读员」。根据检索到的《义务教育数学课程标准》片段，"
        "提取与本课时最相关的核心素养、学业要求、内容要点与教学提示。"
        "必须基于给定片段，禁止编造课标原文；引用写入 citations。"
        "若片段不足，降低 confidence，并只写有依据的内容。"
    )
    user = (
        f"课时信息:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
        f"课标检索片段（由 search_curriculum 工具返回）:\n{context}\n\n"
        "请输出结构化课标解读。"
    )
    analysis = _invoke_structured(system, user, CurriculumAnalysis, temperature=0.1)
    return analysis, context


def run_lesson_plan_agent(
    lesson: LessonInput,
    curriculum: CurriculumAnalysis,
) -> LessonPlan:
    profile = lesson.learning_profile
    if MOCK_LLM:
        # 故意让环节时长之和偏离课时，便于演示「质检不通过 → 回修一次」
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
                    duration_minutes=8,
                    teacher_activity="创设情境，提出核心问题",
                    student_activity="观察、猜想",
                    purpose="激发兴趣，引出课题",
                    board_hint="课题标题",
                    slide_hint="情境图",
                ),
                LessonStage(
                    name="新授",
                    duration_minutes=25,
                    teacher_activity="讲解概念与例题，组织探究",
                    student_activity="合作讨论、归纳",
                    purpose="突破重点",
                    board_hint="概念+例题",
                    slide_hint="定义与步骤",
                ),
                LessonStage(
                    name="巩固",
                    duration_minutes=18,
                    teacher_activity="组织分层练习并点评",
                    student_activity="独立练习、互评",
                    purpose="落实目标",
                    board_hint="易错提醒",
                    slide_hint="练习页",
                ),
                LessonStage(
                    name="小结",
                    duration_minutes=10,
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


def _rule_issues_for_lesson_plan(
    lesson: LessonInput,
    plan: LessonPlan,
    curriculum: CurriculumAnalysis,
) -> tuple[list[str], list[str]]:
    """规则质检：返回 (issues, suggested_fixes)。"""
    issues: list[str] = []
    fixes: list[str] = []

    if not plan.teaching_objectives:
        issues.append("教学目标为空")
        fixes.append("补充至少 2 条可观测的教学目标")
    if not plan.key_points:
        issues.append("教学重点为空")
        fixes.append("根据课题与课标内容要点补全重点")
    if not plan.difficult_points:
        issues.append("教学难点为空")
        fixes.append("结合学情或概念易错点写出难点")
    if len(plan.stages) < 3:
        issues.append(f"教学环节过少（当前 {len(plan.stages)} 个）")
        fixes.append("至少包含导入、新授、巩固等 3 个以上环节")

    total = sum(int(s.duration_minutes or 0) for s in plan.stages)
    target = int(lesson.duration_minutes or 45)
    if plan.stages and abs(total - target) > 8:
        issues.append(f"环节时长之和为 {total} 分钟，与课时 {target} 分钟相差过大")
        fixes.append(f"调整各环节时长，使总和接近 {target} 分钟")

    empty_stage = [s.name for s in plan.stages if not (s.teacher_activity and s.student_activity)]
    if empty_stage:
        issues.append(f"环节活动描述不完整：{'、'.join(empty_stage)}")
        fixes.append("为每个环节补全教师活动与学生活动")

    if curriculum.content_points and plan.key_points:
        blob = " ".join(plan.key_points + plan.teaching_objectives)
        hit = sum(1 for p in curriculum.content_points[:6] if any(tok in blob for tok in _content_tokens(p)))
        if hit == 0 and len(curriculum.content_points) >= 2:
            issues.append("教案目标/重点与课标内容要点关联较弱")
            fixes.append("在教学目标或重点中体现课标内容要点关键词")

    meta_hits: list[str] = []
    for obj in plan.teaching_objectives:
        if _text_has_meta(obj):
            meta_hits.append("教学目标")
            break
    for s in plan.stages:
        if _text_has_meta(s.purpose or "") or _text_has_meta(s.teacher_activity or ""):
            meta_hits.append(f"环节「{s.name}」")
    for idea in plan.assessment_ideas or []:
        if _text_has_meta(idea):
            meta_hits.append("评价建议")
            break
    if meta_hits:
        issues.append(
            "教案正文含审核/质检过程元信息（"
            + "、".join(dict.fromkeys(meta_hits))
            + "），不属于课堂教学内容"
        )
        fixes.append("删除「质检意见」「进入后续组卷」等过程用语，改写为可直接上课的目标与活动")

    return issues, fixes


def _content_tokens(text: str) -> list[str]:
    text = (text or "").strip()
    if len(text) <= 4:
        return [text] if text else []
    # 取较长片段做弱匹配
    return [text[:6], text[-6:]] if len(text) >= 6 else [text]


def review_lesson_plan(
    lesson: LessonInput,
    plan: LessonPlan,
    curriculum: CurriculumAnalysis,
) -> LessonPlanQAReport:
    """教案质检：规则为主；非 MOCK 时再用模型补充意见。passed 仅由实质硬伤决定。"""
    issues, fixes = _rule_issues_for_lesson_plan(lesson, plan, curriculum)

    if not MOCK_LLM:
        try:
            system = (
                "你是「教案审核员」。只审核、不直接改写教案；检查是否可上课、是否对齐课标与课时。\n"
                "只指出明确硬伤，不要空泛夸奖，也不要把「未发现问题」写进 issues。\n"
                "若没有硬伤：passed=true，issues 必须为空列表。\n"
                "有硬伤：passed=false，issues 写具体问题。\n"
                "issues / suggested_fixes 用中文短句；不要提及质检流程本身。"
            )
            user = (
                f"教师输入:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
                f"课标解读:\n{curriculum.model_dump_json(ensure_ascii=False)}\n\n"
                f"待检教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
                f"规则质检已发现: {issues or ['（无）']}"
            )
            llm_report = _invoke_structured(system, user, LessonPlanQAReport, temperature=0.1)
            for issue in llm_report.issues:
                if issue and issue not in issues and not _is_non_issue(issue):
                    issues.append(issue)
            for fix in llm_report.suggested_fixes:
                if fix and fix not in fixes:
                    fixes.append(fix)
            notes = llm_report.notes or ""
            passed, substantive, notes = _finalize_pass(issues, notes=notes)
            return LessonPlanQAReport(
                passed=passed,
                issues=substantive,
                suggested_fixes=fixes if not passed else [],
                revised=False,
                notes=notes,
            )
        except Exception as exc:  # noqa: BLE001
            safe_log(f"  教案 LLM 质检失败，回退规则结果: {exc}")

    passed, substantive, notes = _finalize_pass(
        issues,
        notes="规则质检" if MOCK_LLM else "规则质检（模型质检不可用时）",
    )
    return LessonPlanQAReport(
        passed=passed,
        issues=substantive,
        suggested_fixes=fixes if not passed else [],
        revised=False,
        notes=notes,
    )


def revise_lesson_plan(
    lesson: LessonInput,
    plan: LessonPlan,
    curriculum: CurriculumAnalysis,
    qa: LessonPlanQAReport,
) -> LessonPlan:
    """根据质检意见回修教案（仅一次）。"""
    if MOCK_LLM:
        target = int(lesson.duration_minutes or 45)
        # 按比例压到目标课时
        stages = list(plan.stages) or []
        if not stages:
            return run_lesson_plan_agent(lesson, curriculum)
        raw = [max(3, int(s.duration_minutes or 5)) for s in stages]
        total = sum(raw) or 1
        scaled = [max(3, int(round(target * x / total))) for x in raw]
        drift = target - sum(scaled)
        scaled[-1] = max(3, scaled[-1] + drift)
        fixed_stages = [
            s.model_copy(
                update={
                    "duration_minutes": scaled[i],
                    "purpose": _scrub_meta_text(s.purpose),
                    "teacher_activity": _scrub_meta_text(s.teacher_activity),
                }
            )
            for i, s in enumerate(stages)
        ]
        return plan.model_copy(
            update={
                "stages": fixed_stages,
                "teaching_objectives": [
                    _scrub_meta_text(x)
                    for x in (
                        plan.teaching_objectives or [f"理解{lesson.lesson_title}"]
                    )
                ],
                "key_points": plan.key_points or [lesson.lesson_title],
                "difficult_points": plan.difficult_points or ["灵活应用"],
                "assessment_ideas": [_scrub_meta_text(x) for x in (plan.assessment_ideas or [])],
            }
        )

    system = (
        "你是「教案设计师」。根据质检意见修订教案，输出完整教案 JSON。\n"
        "必须逐条回应质检问题；环节时长之和应接近课时；保留合理原有设计。\n"
        "严禁在教学目标、环节目的、教师活动、评价建议中写入"
        "「质检意见」「进入后续组卷」「回应质检」「审核打回」等过程元信息；"
        "只写可直接用于课堂教学的内容。"
    )
    user = (
        f"教师输入:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
        f"课标解读:\n{curriculum.model_dump_json(ensure_ascii=False)}\n\n"
        f"原教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        f"质检意见:\n{qa.model_dump_json(ensure_ascii=False)}\n\n"
        "请输出修订后的完整教案。"
    )
    revised = _invoke_structured(system, user, LessonPlan, temperature=0.25)
    return _scrub_lesson_plan_meta(revised)


def _scrub_meta_text(text: str) -> str:
    t = (text or "").strip()
    if not t or not _text_has_meta(t):
        return t
    # 含元信息的句子整句丢掉，避免半截残留
    parts = re.split(r"[；;。\n]", t)
    kept = [p.strip() for p in parts if p.strip() and not _text_has_meta(p)]
    return "；".join(kept) if kept else ""


def _scrub_lesson_plan_meta(plan: LessonPlan) -> LessonPlan:
    stages = [
        s.model_copy(
            update={
                "purpose": _scrub_meta_text(s.purpose) or s.purpose,
                "teacher_activity": _scrub_meta_text(s.teacher_activity) or s.teacher_activity,
                "student_activity": _scrub_meta_text(s.student_activity) or s.student_activity,
            }
        )
        for s in plan.stages
    ]
    objs = [_scrub_meta_text(x) or x for x in plan.teaching_objectives]
    objs = [x for x in objs if x]
    return plan.model_copy(
        update={
            "stages": stages,
            "teaching_objectives": objs or plan.teaching_objectives,
            "assessment_ideas": [
                _scrub_meta_text(x) or x for x in (plan.assessment_ideas or []) if x
            ],
        }
    )


def _bank_item_to_exercise(raw: dict, index: int) -> ExerciseItem:
    qtype = str(raw.get("question_type") or "calculation")
    if qtype not in {"choice", "fill", "short", "calculation", "application"}:
        qtype = "calculation"
    diff = str(raw.get("difficulty") or "medium")
    if diff not in {"easy", "medium", "hard"}:
        diff = "medium"
    return ExerciseItem(
        index=index,
        question_type=qtype,  # type: ignore[arg-type]
        difficulty=diff,  # type: ignore[arg-type]
        knowledge_point=str(raw.get("knowledge_point") or ""),
        stem=str(raw.get("stem") or ""),
        options=[str(o) for o in (raw.get("options") or [])],
        answer=str(raw.get("answer") or ""),
        analysis=str(raw.get("analysis") or ""),
        score=int(raw.get("score") or 5),
        source="bank",
        source_id=str(raw.get("id") or ""),
    )


def _mock_exercise_from_bank(lesson: LessonInput, plan: LessonPlan) -> ExercisePaper:
    """MOCK：从题库按课题抽 easy/medium/hard，不足再补占位题。"""
    query = f"{lesson.grade} {lesson.unit} {lesson.lesson_title}"
    picked: list[dict] = []
    seen: set[str] = set()
    for diff in ("easy", "medium", "hard"):
        for hit in qb_search(query, grade=lesson.grade, difficulty=diff, k=2):
            hid = str(hit.get("id") or "")
            if hid and hid not in seen:
                seen.add(hid)
                picked.append(hit)
            if len(picked) >= 10:
                break
        if len(picked) >= 10:
            break
    if len(picked) < 6:
        for hit in qb_search(query, grade=lesson.grade, k=10):
            hid = str(hit.get("id") or "")
            if hid and hid not in seen:
                seen.add(hid)
                picked.append(hit)

    items = [_bank_item_to_exercise(h, i) for i, h in enumerate(picked[:10], start=1)]
    if not items:
        items = [
            ExerciseItem(
                index=1,
                question_type="calculation",
                difficulty="medium",
                knowledge_point=lesson.lesson_title,
                stem=f"完成与「{lesson.lesson_title}」相关的基础练习（题库为空时的占位题）。",
                answer="（示例）",
                analysis="请补充题库后重新生成。",
                score=10,
                source="generated",
            )
        ]

    easy = sum(1 for x in items if x.difficulty == "easy")
    medium = sum(1 for x in items if x.difficulty == "medium")
    hard = sum(1 for x in items if x.difficulty == "hard")
    bank_ids = [x.source_id for x in items if x.source_id]
    return ExercisePaper(
        title=f"{lesson.lesson_title}·随堂练习",
        total_score=sum(x.score for x in items),
        time_limit_minutes=15,
        difficulty_distribution=DifficultyDistribution(easy=easy, medium=medium, hard=hard),
        knowledge_coverage=plan.key_points[:3] or [lesson.lesson_title],
        items=items,
        design_notes=(
            f"MOCK：优先题库选题（{', '.join(bank_ids) or '无'}）；"
            "基础→巩固→拓展，可按班级水平删减。"
        ),
    )


def _gather_questions_via_tools(
    lesson: LessonInput,
    plan: LessonPlan,
    max_rounds: int = 5,
    anchors: list[TeachingAnchor] | None = None,
) -> str:
    """让习题组卷师按需调用 search_question_bank，汇总候选题。"""
    bound = resolve_teaching_anchors(plan, lesson, anchors)
    kp = "、".join(a.knowledge_point for a in bound) or "、".join(plan.key_points[:4]) or lesson.lesson_title
    intents = "、".join(plan.practice_intents[:4])
    system = (
        "你是中小学数学「习题组卷师」的检索助手。\n"
        "必须使用工具 search_question_bank 从本地题库检索候选题，不要凭空编造题库原文。\n"
        "检索词必须来自硬约束三元组中的知识点，难度与标注层级一致：基础→easy，进阶→medium，拓展→hard。\n"
        "检索足够后停止，简短回复「检索完成」即可。"
    )
    user = (
        f"课时信息:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
        f"教案重点: {kp}\n"
        f"教案难点: {'、'.join(plan.difficult_points[:3])}\n"
        f"练习意图: {intents}\n\n"
        "请先调用 search_question_bank 检索题库，再结束。"
    )
    system, user = _apply_anchor_constraint(system, user, bound)
    chunks: list[str] = []
    for anchor in bound[:6]:
        chunks.append(
            search_question_bank.invoke(
                {
                    "query": anchor.knowledge_point,
                    "grade": lesson.grade,
                    "difficulty": _BANK_DIFFICULTY.get(anchor.difficulty, "medium"),
                    "k": 4,
                }
            )
        )
    chunks.extend(
        _run_tool_loop(
            tools=[search_question_bank],
            system=system,
            user=user,
            temperature=0.15,
            max_rounds=max_rounds,
            first_tool_choice="search_question_bank",
        )
    )

    if not chunks:
        safe_log("  未获得题库工具结果，使用默认查询兜底")
        base = f"{lesson.grade} {lesson.unit} {lesson.lesson_title}"
        for diff in ("easy", "medium", "hard"):
            chunks.append(
                search_question_bank.invoke(
                    {
                        "query": base,
                        "grade": lesson.grade,
                        "difficulty": diff,
                        "k": 8,
                    }
                )
            )

    seen: set[str] = set()
    unique: list[str] = []
    for c in chunks:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return "\n\n---\n\n".join(unique)


def run_exercise_agent(
    lesson: LessonInput,
    plan: LessonPlan,
    anchors: list[TeachingAnchor] | None = None,
) -> ExercisePaper:
    """习题组卷师：先读取教学三元组，再检索题库、选题/改编/补生成练习卷。"""
    profile = lesson.learning_profile
    bound = resolve_teaching_anchors(plan, lesson, anchors)
    if MOCK_LLM:
        # 故意产出偏弱初稿，便于演示「对照教案质检 → 回修一次」
        paper = _mock_exercise_from_bank(lesson, plan)
        weak_items = paper.items[:2] if len(paper.items) >= 2 else paper.items
        for i, it in enumerate(weak_items, start=1):
            it.index = i
        kps = "、".join(a.knowledge_point for a in bound)
        return paper.model_copy(
            update={
                "items": weak_items,
                "knowledge_coverage": [],
                "total_score": sum(x.score for x in weak_items),
                "difficulty_distribution": DifficultyDistribution(
                    easy=sum(1 for x in weak_items if x.difficulty == "easy"),
                    medium=sum(1 for x in weak_items if x.difficulty == "medium"),
                    hard=sum(1 for x in weak_items if x.difficulty == "hard"),
                ),
                "design_notes": (paper.design_notes or "")
                + f"（初稿：待质检；已读取三元组硬约束：{kps}）",
            }
        )

    focus_hint = {
        "foundation": "偏重基础巩固，少拓展；题库优先选 easy",
        "key_points": "围绕重难点突破，难度梯度清晰",
        "extension": "增加变式与综合应用；可多选 hard",
    }.get(profile.focus, "难度梯度清晰")

    bank_context = _gather_questions_via_tools(lesson, plan, anchors=bound)
    system = (
        "你是中小学数学「习题组卷师」。根据教案与题库检索结果组出一课时练习卷。\n"
        "规则：\n"
        "1) 优先选用题库题目：复制题干/选项/答案/解析，source=\"bank\"，source_id=题库 id；\n"
        "2) 可对题库题做轻微改编（数字/情境），仍标 source=\"bank\" 并保留 source_id；\n"
        "3) 题库不足或需补梯度时，可原创补题，source=\"generated\"，source_id 留空；\n"
        "4) design_notes 中写明：选用了哪些 source_id、哪些题为补生成；\n"
        "5) 难度分布 easy/medium/hard 与 items 实际一致；选择题必须给 options；\n"
        "6) items 至少 6 题，建议 8～10 题（题库充足时尽量多选），total_score 等于各题 score 之和；\n"
        "7) 不要声称来自商业题库；演示题库即可如实标注。"
    )
    user = (
        f"教师输入:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
        f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        f"组卷侧重：{focus_hint}\n\n"
        f"题库检索结果（由 search_question_bank 返回）:\n{bank_context}\n\n"
        "请输出结构化练习卷。"
    )
    system, user = _apply_anchor_constraint(system, user, bound)
    return _invoke_structured(system, user, ExercisePaper, temperature=0.3)


def _kp_tokens(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"[\s,，、;；|/]+", text)
    tokens: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < 2:
            continue
        tokens.append(p)
        if re.search(r"[\u4e00-\u9fff]", p) and len(p) >= 2:
            tokens.extend(p[i : i + 2] for i in range(len(p) - 1))
    return tokens


def _item_blob(item: ExerciseItem) -> str:
    return " ".join(
        [
            item.knowledge_point or "",
            item.stem or "",
            item.analysis or "",
            " ".join(item.options or []),
        ]
    )


def _rule_issues_for_exercise(
    lesson: LessonInput,
    plan: LessonPlan,
    paper: ExercisePaper,
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    fixes: list[str] = []
    items = paper.items or []

    if len(items) < 4:
        issues.append(f"题目过少（当前 {len(items)} 题，至少 4 题）")
        fixes.append("补足至 6～10 题，覆盖基础/巩固/拓展")

    choice_bad = [
        f"第{it.index}题" for it in items if it.question_type == "choice" and len(it.options or []) < 2
    ]
    if choice_bad:
        issues.append(f"选择题缺少选项：{'、'.join(choice_bad)}")
        fixes.append("为选择题补充完整 options")

    empty_answer = [f"第{it.index}题" for it in items if not (it.answer or "").strip()]
    if empty_answer:
        issues.append(f"缺少答案：{'、'.join(empty_answer[:5])}")
        fixes.append("补全每题 answer")

    score_sum = sum(int(it.score or 0) for it in items)
    if items and int(paper.total_score or 0) != score_sum:
        issues.append(f"总分 {paper.total_score} 与各题分值之和 {score_sum} 不一致")
        fixes.append(f"将 total_score 改为 {score_sum}，或调整各题 score")

    easy = sum(1 for it in items if it.difficulty == "easy")
    medium = sum(1 for it in items if it.difficulty == "medium")
    hard = sum(1 for it in items if it.difficulty == "hard")
    dist = paper.difficulty_distribution
    if dist and (dist.easy != easy or dist.medium != medium or dist.hard != hard):
        issues.append(
            f"难度分布字段({dist.easy}/{dist.medium}/{dist.hard})与实际题量({easy}/{medium}/{hard})不一致"
        )
        fixes.append("按实际 items 重写 difficulty_distribution")

    # 对照教案重点覆盖
    coverage_blob = " ".join(paper.knowledge_coverage or []) + " " + " ".join(
        _item_blob(it) for it in items
    )
    missed: list[str] = []
    for kp in (plan.key_points or [])[:4]:
        toks = [t for t in _kp_tokens(kp) if len(t) >= 2]
        if not toks:
            continue
        if not any(t in coverage_blob for t in toks):
            missed.append(kp)
    if missed:
        issues.append(f"未充分覆盖教案重点：{'、'.join(missed)}")
        fixes.append("增补对应知识点题目，并写入 knowledge_coverage")

    intents = plan.practice_intents or []
    if intents and items:
        intent_hit = 0
        for intent in intents[:4]:
            toks = [t for t in _kp_tokens(intent) if len(t) >= 2]
            if toks and any(t in coverage_blob for t in toks):
                intent_hit += 1
        # 练习意图往往较抽象，只在完全无关时提示
        if intent_hit == 0 and len(intents) >= 2:
            issues.append("练习卷与教案 practice_intents 关联较弱")
            fixes.append("按练习意图调整题型（如说理/应用）或在 design_notes 说明对应关系")

    focus = lesson.learning_profile.focus
    if focus == "foundation" and hard > easy and len(items) >= 3:
        issues.append("学情侧重 foundation，但难题多于易题")
        fixes.append("减少 hard，增加 easy/medium 基础巩固题")
    if focus == "extension" and hard == 0 and len(items) >= 4:
        issues.append("学情侧重 extension，但缺少难题")
        fixes.append("至少增加 1～2 道 hard 拓展/综合题")

    if not (paper.knowledge_coverage or []):
        issues.append("knowledge_coverage 为空")
        fixes.append("填写本卷覆盖的知识点列表（应对齐教案重点）")

    return issues, fixes


def review_exercise_paper(
    lesson: LessonInput,
    plan: LessonPlan,
    paper: ExercisePaper,
) -> LessonPlanQAReport:
    """习题对照教案质检：规则为主；非 MOCK 时模型补充。"""
    issues, fixes = _rule_issues_for_exercise(lesson, plan, paper)

    if not MOCK_LLM:
        try:
            system = (
                "你是「习题质检员」。对照教案检查练习卷是否为本课时服务。\n"
                "关注：知识点覆盖、难度与学情、题量、选择题选项、总分一致性。\n"
                "passed=true 表示可进入课件设计；有硬伤则 passed=false。"
            )
            user = (
                f"教师输入:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
                f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
                f"练习卷:\n{paper.model_dump_json(ensure_ascii=False)}\n\n"
                f"规则质检已发现: {issues or ['（无）']}"
            )
            llm_report = _invoke_structured(system, user, LessonPlanQAReport, temperature=0.1)
            for issue in llm_report.issues:
                if issue and issue not in issues:
                    issues.append(issue)
            for fix in llm_report.suggested_fixes:
                if fix and fix not in fixes:
                    fixes.append(fix)
            hard = any(
                key in iss
                for iss in issues
                for key in ("过少", "缺少", "不一致", "未充分覆盖", "为空")
            )
            passed = (bool(llm_report.passed) and not issues) if not hard else False
            return LessonPlanQAReport(
                passed=passed,
                issues=issues,
                suggested_fixes=fixes,
                revised=False,
                notes=llm_report.notes or "",
            )
        except Exception as exc:  # noqa: BLE001
            safe_log(f"  习题 LLM 质检失败，回退规则结果: {exc}")

    return LessonPlanQAReport(
        passed=len(issues) == 0,
        issues=issues,
        suggested_fixes=fixes,
        revised=False,
        notes="规则质检（对照教案）" if MOCK_LLM else "规则质检（模型质检不可用时）",
    )


def revise_exercise_paper(
    lesson: LessonInput,
    plan: LessonPlan,
    paper: ExercisePaper,
    qa: LessonPlanQAReport,
    anchors: list[TeachingAnchor] | None = None,
) -> ExercisePaper:
    """根据质检意见回修练习卷（仅一次），仍受教学三元组约束。"""
    bound = resolve_teaching_anchors(plan, lesson, anchors)
    if MOCK_LLM:
        fixed = _mock_exercise_from_bank(lesson, plan)
        coverage = list(
            dict.fromkeys(
                [a.knowledge_point for a in bound][:4]
                + (plan.key_points or [])[:4]
                + (fixed.knowledge_coverage or [])
            )
        )
        if not coverage:
            coverage = [lesson.lesson_title]
        easy = sum(1 for x in fixed.items if x.difficulty == "easy")
        medium = sum(1 for x in fixed.items if x.difficulty == "medium")
        hard = sum(1 for x in fixed.items if x.difficulty == "hard")
        return fixed.model_copy(
            update={
                "knowledge_coverage": coverage,
                "total_score": sum(x.score for x in fixed.items),
                "difficulty_distribution": DifficultyDistribution(
                    easy=easy, medium=medium, hard=hard
                ),
                "design_notes": (
                    (fixed.design_notes or "")
                    + f"；已按质检回修：{'; '.join(qa.issues[:3]) or '补足题量与覆盖'}"
                    + "；回修仍遵守三元组硬约束"
                ),
            }
        )

    bank_context = _gather_questions_via_tools(lesson, plan, max_rounds=3, anchors=bound)
    system = (
        "你是「习题组卷师」。根据质检意见修订练习卷，输出完整 ExercisePaper JSON。\n"
        "必须覆盖教案重点；修正题量/选项/总分/难度分布；优先使用题库检索结果。"
    )
    user = (
        f"教师输入:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
        f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        f"原练习卷:\n{paper.model_dump_json(ensure_ascii=False)}\n\n"
        f"质检意见:\n{qa.model_dump_json(ensure_ascii=False)}\n\n"
        f"题库检索结果:\n{bank_context}\n\n"
        "请输出修订后的完整练习卷。"
    )
    system, user = _apply_anchor_constraint(system, user, bound)
    return _invoke_structured(system, user, ExercisePaper, temperature=0.25)


def _build_mock_slides(lesson: LessonInput, plan: LessonPlan) -> Slides:
    """按教案环节生成对齐的 MOCK 课件。"""
    img_raw = search_images.invoke(
        {"query": f"{lesson.lesson_title} math education", "limit": 1}
    )
    diagram_raw = generate_diagram.invoke(
        {"prompt": f"{lesson.lesson_title} 解题流程：审题→计算→检验"}
    )
    img_id = json.loads(img_raw).get("media_id", "")
    diagram_id = json.loads(diagram_raw).get("media_id", "")

    pages = []
    for i, stage in enumerate(plan.stages, start=1):
        use_diagram = i == 2 and diagram_id
        pages.append(
            SlidePage(
                index=i,
                title=f"{stage.name}：{lesson.lesson_title}",
                bullets=[stage.purpose, (stage.teacher_activity or "")[:40]],
                interaction=stage.student_activity,
                visual_keywords=[lesson.lesson_title, stage.name, "示意图"],
                media_type_suggestion="diagram" if use_diagram else "image",
                linked_stage=stage.name,
                image_id=diagram_id if use_diagram else (img_id if i == 1 else ""),
                image_source=(
                    "generated"
                    if use_diagram
                    else ("search" if i == 1 and img_id else "none")
                ),
                image_caption=stage.name,
            )
        )
    return attach_media_to_slides(
        Slides(
            pages=pages,
            design_notes="简洁清晰，一页一个重点；部分页面已绑定 search_images / generate_diagram 素材。",
        ),
        parse_media_manifest([img_raw, diagram_raw]),
    )


def run_slides_agent(
    lesson: LessonInput,
    plan: LessonPlan,
    anchors: list[TeachingAnchor] | None = None,
) -> Slides:
    bound = resolve_teaching_anchors(plan, lesson, anchors)
    if MOCK_LLM:
        # 故意不对齐环节，便于演示「对照环节质检 → 回修一次」
        good = _build_mock_slides(lesson, plan)
        kps = "、".join(a.knowledge_point for a in bound)
        if not good.pages:
            return good.model_copy(
                update={"design_notes": (good.design_notes or "") + f"；已读取三元组硬约束：{kps}"}
            )
        weak_pages = good.pages[: max(1, len(good.pages) // 2)]
        for i, p in enumerate(weak_pages, start=1):
            p.index = i
            p.linked_stage = "未命名环节"
            p.bullets = []
        return Slides(
            pages=weak_pages,
            design_notes=(good.design_notes or "") + f"（初稿：待质检；已读取三元组硬约束：{kps}）",
        )

    media_context, media_manifest = _gather_slide_media_via_tools(lesson, plan)
    system = (
        "你是「课件生成师」。根据教案与已获取的配图素材，生成 PPT 大纲。\n"
        "每页包含：标题、要点 bullets、互动提示、visual_keywords、素材类型 media_type_suggestion、"
        "对应环节 linked_stage。\n"
        "配图已由工具准备好，你无需填写 image_id；只需标注哪些页适合 diagram/image。\n"
        "规则：页面应覆盖教案各教学环节；linked_stage 必须使用教案中的环节名称；每页 bullets 非空。\n"
        "风格适合课堂投影，少字多图。"
    )
    user = (
        f"课题: {lesson.lesson_title}\n"
        f"学段年级: {lesson.stage} {lesson.grade}\n"
        f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        f"已获取配图素材（共 {len(media_manifest)} 个，工具返回 JSON）:\n{media_context}\n\n"
        "请输出课件大纲。"
    )
    system, user = _apply_anchor_constraint(system, user, bound)
    slides = _invoke_structured(system, user, Slides, temperature=0.3)
    return attach_media_to_slides(slides, media_manifest)


def _rule_issues_for_slides(
    lesson: LessonInput,
    plan: LessonPlan,
    slides: Slides,
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    fixes: list[str] = []
    pages = slides.pages or []
    stage_names = [s.name for s in (plan.stages or []) if s.name]

    if not pages:
        issues.append("课件页为空")
        fixes.append("按教案每个环节至少生成 1 页")
        return issues, fixes

    if stage_names and len(pages) < max(2, len(stage_names) - 1):
        issues.append(
            f"课件页过少（{len(pages)} 页），教案环节有 {len(stage_names)} 个"
        )
        fixes.append("为每个主要教学环节补充对应幻灯片")

    empty_bullets = [f"第{p.index}页" for p in pages if not (p.bullets or [])]
    if empty_bullets:
        issues.append(f"要点为空：{'、'.join(empty_bullets[:5])}")
        fixes.append("为每页补充 1～3 条 bullets")

    linked = [(p.linked_stage or "").strip() for p in pages]
    if stage_names:
        unknown = sorted({x for x in linked if x and x not in stage_names})
        if unknown:
            issues.append(f"linked_stage 不在教案环节中：{'、'.join(unknown)}")
            fixes.append(f"将 linked_stage 改为教案环节名：{'、'.join(stage_names)}")

        covered = {x for x in linked if x in stage_names}
        missed = [n for n in stage_names if n not in covered]
        # 允许少覆盖 1 个次要环节；缺一半以上算硬伤
        if missed and len(missed) >= max(1, (len(stage_names) + 1) // 2):
            issues.append(f"未覆盖教案环节：{'、'.join(missed)}")
            fixes.append("为缺失环节各补至少 1 页，并正确填写 linked_stage")

    # 新授/巩固类关键页尽量有图或明确媒体类型
    key_pages = [
        p
        for p in pages
        if any(k in (p.linked_stage or p.title or "") for k in ("新授", "巩固", "探究", "练习"))
    ]
    if key_pages:
        bare = [
            f"第{p.index}页"
            for p in key_pages
            if not (p.image_id or "").strip() and (p.media_type_suggestion or "none") == "none"
        ]
        if bare:
            issues.append(f"关键环节页缺少配图建议：{'、'.join(bare[:4])}")
            fixes.append("为新授/巩固页设置 media_type_suggestion=diagram/image 或绑定素材")

    if lesson.lesson_title:
        titled = sum(1 for p in pages if lesson.lesson_title in (p.title or ""))
        if titled == 0 and len(pages) >= 2:
            issues.append("多数页面标题未体现课题名称")
            fixes.append(f"在标题中带上课题「{lesson.lesson_title}」以便投影辨识")

    return issues, fixes


def review_slides(
    lesson: LessonInput,
    plan: LessonPlan,
    slides: Slides,
) -> LessonPlanQAReport:
    """课件对照教案环节质检。"""
    issues, fixes = _rule_issues_for_slides(lesson, plan, slides)

    if not MOCK_LLM:
        try:
            system = (
                "你是「课件质检员」。对照教案检查 PPT 大纲是否对齐教学环节。\n"
                "关注：页数与环节覆盖、linked_stage 是否使用教案环节名、bullets 是否为空、"
                "新授/巩固是否有配图建议。\n"
                "passed=true 表示可进入板书设计；有硬伤则 passed=false。"
            )
            user = (
                f"教师输入:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
                f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
                f"课件:\n{slides.model_dump_json(ensure_ascii=False)}\n\n"
                f"规则质检已发现: {issues or ['（无）']}"
            )
            llm_report = _invoke_structured(system, user, LessonPlanQAReport, temperature=0.1)
            for issue in llm_report.issues:
                if issue and issue not in issues:
                    issues.append(issue)
            for fix in llm_report.suggested_fixes:
                if fix and fix not in fixes:
                    fixes.append(fix)
            hard = any(
                key in iss
                for iss in issues
                for key in ("为空", "过少", "不在教案", "未覆盖")
            )
            passed = (bool(llm_report.passed) and not issues) if not hard else False
            return LessonPlanQAReport(
                passed=passed,
                issues=issues,
                suggested_fixes=fixes,
                revised=False,
                notes=llm_report.notes or "",
            )
        except Exception as exc:  # noqa: BLE001
            safe_log(f"  课件 LLM 质检失败，回退规则结果: {exc}")

    return LessonPlanQAReport(
        passed=len(issues) == 0,
        issues=issues,
        suggested_fixes=fixes,
        revised=False,
        notes="规则质检（对照教案环节）" if MOCK_LLM else "规则质检（模型质检不可用时）",
    )


def revise_slides(
    lesson: LessonInput,
    plan: LessonPlan,
    slides: Slides,
    qa: LessonPlanQAReport,
    anchors: list[TeachingAnchor] | None = None,
) -> Slides:
    """根据质检意见回修课件（仅一次），仍受教学三元组约束。"""
    bound = resolve_teaching_anchors(plan, lesson, anchors)
    if MOCK_LLM:
        fixed = _build_mock_slides(lesson, plan)
        kps = "、".join(a.knowledge_point for a in bound)
        return fixed.model_copy(
            update={
                "design_notes": (
                    (fixed.design_notes or "")
                    + f"；已按质检回修：{'; '.join(qa.issues[:3]) or '对齐教案环节'}"
                    + f"；仍遵守三元组：{kps}"
                )
            }
        )

    media_context, media_manifest = _gather_slide_media_via_tools(lesson, plan, max_rounds=4)
    system = (
        "你是「课件生成师」。根据质检意见修订 PPT 大纲，输出完整 Slides JSON。\n"
        "必须覆盖教案各环节；linked_stage 使用教案环节原名；每页 bullets 非空。"
    )
    user = (
        f"教师输入:\n{lesson.model_dump_json(ensure_ascii=False)}\n\n"
        f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        f"原课件:\n{slides.model_dump_json(ensure_ascii=False)}\n\n"
        f"质检意见:\n{qa.model_dump_json(ensure_ascii=False)}\n\n"
        f"配图素材:\n{media_context}\n\n"
        "请输出修订后的完整课件大纲。"
    )
    system, user = _apply_anchor_constraint(system, user, bound)
    revised = _invoke_structured(system, user, Slides, temperature=0.25)
    return attach_media_to_slides(revised, media_manifest)


def run_blackboard_agent(
    lesson: LessonInput,
    plan: LessonPlan,
    anchors: list[TeachingAnchor] | None = None,
) -> Blackboard:
    bound = resolve_teaching_anchors(plan, lesson, anchors)
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
            key_sentences=[f"本节核心：{a.knowledge_point}" for a in bound]
            or [f"本节核心：{lesson.lesson_title}"],
            linked_stages=[s.name for s in plan.stages],
        )

    system = (
        "你是「板书设计师」。根据教案设计课堂板书："
        "布局、主板书条目（含书写顺序）、副板书、书写序列与关键句。"
        "板书应简洁、层次清楚，适合边讲边写。"
        "linked_stages 必须使用教案中的环节名称。"
    )
    user = (
        f"课题: {lesson.lesson_title}\n"
        f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        "请输出板书设计。"
    )
    system, user = _apply_anchor_constraint(system, user, bound)
    return _invoke_structured(system, user, Blackboard, temperature=0.3)


def _rule_issues_for_consistency(
    lesson: LessonInput,
    plan: LessonPlan,
    paper: ExercisePaper,
    slides: Slides,
    board: Blackboard,
    *,
    active_modules: set[str] | None = None,
    anchors: list[TeachingAnchor] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """返回 (issues, fixes, conflict_modules)。

    active_modules：仅检查已启用的材料插件；None 表示三者全检（兼容旧行为）。
    anchors：触发 skill:consistency.anchors，对照教学三元组。
    """
    check = active_modules or {"exercises", "slides", "blackboard"}
    issues: list[str] = []
    fixes: list[str] = []
    conflicts: set[str] = set()
    stage_names = [s.name for s in (plan.stages or []) if s.name]
    key_points = plan.key_points or []
    title = lesson.lesson_title or ""

    if anchors:
        a_issues, a_fixes, a_mods = check_anchors_consistency(
            anchors, paper, slides, board, active_modules=check
        )
        for item in a_issues:
            if item not in issues:
                issues.append(item)
        for item in a_fixes:
            if item not in fixes:
                fixes.append(item)
        conflicts.update(a_mods)

    # --- 习题 vs 教案 ---
    if "exercises" in check:
        coverage_blob = " ".join(paper.knowledge_coverage or []) + " " + " ".join(
            f"{it.knowledge_point} {it.stem}" for it in (paper.items or [])
        )
        if key_points:
            missed_kp = [
                kp
                for kp in key_points[:3]
                if kp and not any(tok in coverage_blob for tok in [kp[:2], kp[-2:]] if len(tok) >= 2)
            ]
            # simpler: substring
            missed_kp = [kp for kp in key_points[:3] if kp and kp not in coverage_blob and not any(c in coverage_blob for c in (kp[i:i+2] for i in range(max(0, len(kp)-1))))]
            if len(missed_kp) >= 2 or (len(key_points) == 1 and missed_kp):
                issues.append(f"习题未充分覆盖教案重点：{'、'.join(missed_kp[:3])}")
                fixes.append("习题组卷师：按教案 key_points 补题并更新 knowledge_coverage")
                conflicts.add("exercises")

        if not (paper.items or []):
            issues.append("习题卷为空")
            fixes.append("习题组卷师：重新生成练习卷")
            conflicts.add("exercises")

    # --- 课件 vs 教案 ---
    pages = slides.pages or []
    if "slides" in check:
        if not pages:
            issues.append("课件页为空")
            fixes.append("课件生成师：按教案环节生成幻灯片")
            conflicts.add("slides")
        else:
            linked = {(p.linked_stage or "").strip() for p in pages}
            if stage_names:
                unknown = sorted(x for x in linked if x and x not in stage_names)
                if unknown:
                    issues.append(f"课件 linked_stage 不在教案环节中：{'、'.join(unknown)}")
                    fixes.append("课件生成师：将 linked_stage 改为教案环节名")
                    conflicts.add("slides")
                covered = {x for x in linked if x in stage_names}
                missed = [n for n in stage_names if n not in covered]
                if missed and len(missed) >= max(1, (len(stage_names) + 1) // 2):
                    issues.append(f"课件未覆盖教案环节：{'、'.join(missed)}")
                    fixes.append("课件生成师：为缺失环节补页")
                    conflicts.add("slides")

    # --- 板书 vs 教案 ---
    board_stages = [s for s in (board.linked_stages or []) if s]
    if "blackboard" in check:
        if stage_names:
            if not board_stages:
                issues.append("板书 linked_stages 为空，未对齐教案环节")
                fixes.append("板书设计师：填写教案环节名称到 linked_stages")
                conflicts.add("blackboard")
            else:
                unknown_b = [s for s in board_stages if s not in stage_names]
                if unknown_b:
                    issues.append(f"板书环节名与教案不一致：{'、'.join(unknown_b)}")
                    fixes.append("板书设计师：linked_stages 使用教案环节原名")
                    conflicts.add("blackboard")

        if not (board.main_board or []):
            issues.append("主板书为空")
            fixes.append("板书设计师：补充主板书条目")
            conflicts.add("blackboard")

    # --- 已启用材料互相对齐 ---
    if "slides" in check and "blackboard" in check and pages and board_stages and stage_names:
        slide_cov = {(p.linked_stage or "").strip() for p in pages if (p.linked_stage or "").strip()}
        board_set = set(board_stages)
        # 课件有而板书完全没有的核心环节
        core = [n for n in stage_names if n in ("新授", "巩固", "小结") or "新授" in n or "巩固" in n]
        if not core:
            core = stage_names[:2]
        for n in core:
            if n in slide_cov and n not in board_set:
                issues.append(f"课件含环节「{n}」，板书未体现")
                fixes.append("板书设计师：补充对应环节到 linked_stages / 主板书")
                conflicts.add("blackboard")

    # 课题标题应在已启用材料中出现
    board_text = " ".join(x.text for x in (board.main_board or [])) + " ".join(
        board.key_sentences or []
    )
    slide_titles = " ".join(p.title or "" for p in pages)
    title_ok = False
    if title:
        if "blackboard" in check and title in board_text:
            title_ok = True
        if "slides" in check and title in slide_titles:
            title_ok = True
        if "exercises" in check and title in (paper.title or ""):
            title_ok = True
        if not title_ok and check:
            issues.append(f"已生成材料标题均未体现课题「{title}」")
            fixes.append("相关设计师：在标题或主板书中写明课题")
            conflicts.update(set(check))

    return issues, fixes, sorted(conflicts)


def review_consistency(
    lesson: LessonInput,
    plan: LessonPlan,
    paper: ExercisePaper,
    slides: Slides,
    board: Blackboard,
    *,
    active_modules: set[str] | None = None,
    anchors: list[TeachingAnchor] | None = None,
) -> ConsistencyReport:
    check = active_modules or {"exercises", "slides", "blackboard"}
    # anchors=None：从教案现提；显式 []：跳过三元组 skill
    if anchors is None:
        bound = resolve_teaching_anchors(plan, lesson, None)
    else:
        bound = list(anchors)
    issues, fixes, modules = _rule_issues_for_consistency(
        lesson,
        plan,
        paper,
        slides,
        board,
        active_modules=check,
        anchors=bound or None,
    )

    if not MOCK_LLM:
        try:
            mods_text = "、".join(sorted(check)) or "（无）"
            system = (
                "你是「一致性检查员」。对照同一教案，检查已启用材料是否一致、可同课使用。\n"
                f"本次仅检查模块：{mods_text}。conflict_modules 只能从这些模块中选。\n"
                "若提供了教学三元组，还必须核对知识点覆盖与难度是否越级（skill:consistency.anchors）。\n"
                "关注：知识点是否同源、环节名是否对齐、有无互相矛盾。\n"
                "若没有硬伤：passed=true，issues 必须为空列表，不要把「未发现矛盾」写进 issues。\n"
                "有硬伤：passed=false，issues 写具体问题。"
            )
            parts = [f"课题: {lesson.lesson_title}", f"教案:\n{plan.model_dump_json(ensure_ascii=False)}"]
            if bound:
                parts.append(
                    "教学三元组（硬约束）:\n"
                    + "\n".join(
                        f"- {a.objective} | {a.knowledge_point} | {a.difficulty}"
                        for a in bound
                    )
                )
            if "exercises" in check:
                parts.append(f"习题:\n{paper.model_dump_json(ensure_ascii=False)}")
            if "slides" in check:
                parts.append(f"课件:\n{slides.model_dump_json(ensure_ascii=False)}")
            if "blackboard" in check:
                parts.append(f"板书:\n{board.model_dump_json(ensure_ascii=False)}")
            parts.append(f"规则检查已发现: {issues or ['（无）']}")
            user = "\n\n".join(parts)
            llm = _invoke_structured(system, user, ConsistencyReport, temperature=0.1)
            for issue in llm.issues:
                if issue and issue not in issues and not _is_non_issue(issue):
                    issues.append(issue)
            for fix in llm.suggested_fixes:
                if fix and fix not in fixes:
                    fixes.append(fix)
            for m in llm.conflict_modules:
                if m in check:
                    modules.append(m)
            modules = sorted(set(modules))
            passed, substantive, notes = _finalize_pass(
                issues, notes=llm.notes or ""
            )
            if passed:
                modules = []
            elif not modules and substantive:
                modules = sorted(check)
            return ConsistencyReport(
                passed=passed,
                issues=substantive,
                suggested_fixes=fixes if not passed else [],
                conflict_modules=modules,  # type: ignore[arg-type]
                revised=False,
                notes=notes,
            )
        except Exception as exc:  # noqa: BLE001
            safe_log(f"  一致性 LLM 检查失败，回退规则: {exc}")

    passed, substantive, notes = _finalize_pass(
        issues,
        notes="规则一致性检查（含 consistency.anchors）"
        if MOCK_LLM
        else "规则一致性检查（模型不可用时）",
    )
    if passed:
        modules = []
    return ConsistencyReport(
        passed=passed,
        issues=substantive,
        suggested_fixes=fixes if not passed else [],
        conflict_modules=modules,  # type: ignore[arg-type]
        revised=False,
        notes=notes,
    )


def revise_blackboard(
    lesson: LessonInput,
    plan: LessonPlan,
    board: Blackboard,
    *,
    issues: list[str],
    anchors: list[TeachingAnchor] | None = None,
) -> Blackboard:
    bound = resolve_teaching_anchors(plan, lesson, anchors)
    if MOCK_LLM:
        fixed = run_blackboard_agent(lesson, plan, bound)
        return fixed.model_copy(
            update={
                "linked_stages": [s.name for s in plan.stages],
                "key_sentences": list(
                    dict.fromkeys(
                        (fixed.key_sentences or [])
                        + [a.knowledge_point for a in bound]
                        + [f"本节核心：{lesson.lesson_title}"]
                        + (plan.key_points[:2] if plan.key_points else [])
                    )
                ),
            }
        )
    system = (
        "你是「板书设计师」。根据一致性检查意见修订板书，输出完整 Blackboard JSON。\n"
        "linked_stages 必须使用教案环节原名；主板书应体现课题与重点。"
    )
    user = (
        f"课题: {lesson.lesson_title}\n"
        f"教案:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        f"原板书:\n{board.model_dump_json(ensure_ascii=False)}\n\n"
        f"一致性意见: {issues}\n\n"
        "请输出修订后的板书。"
    )
    system, user = _apply_anchor_constraint(system, user, bound)
    return _invoke_structured(system, user, Blackboard, temperature=0.25)


def dumps_pretty(model: BaseModel) -> str:
    return json.dumps(model.model_dump(), ensure_ascii=False, indent=2)
