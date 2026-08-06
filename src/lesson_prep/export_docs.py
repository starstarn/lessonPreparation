from __future__ import annotations

import io
import re
from typing import Any


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def _safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or "备课结果"
    return name[:60]


def build_export_basename(lesson_input: dict[str, Any]) -> str:
    grade = str(lesson_input.get("grade") or "")
    title = str(lesson_input.get("lesson_title") or "备课结果")
    return _safe_filename(f"{grade}-{title}".strip("-"))


def _collect_sections(lesson_input: dict[str, Any], result: dict[str, Any]) -> list[tuple[str, list[str]]]:
    curriculum = result.get("curriculum_analysis") or {}
    plan = result.get("lesson_plan") or {}
    slides = result.get("slides") or {}
    board = result.get("blackboard") or {}

    sections: list[tuple[str, list[str]]] = [
        (
            "课时信息",
            [
                f"学段：{lesson_input.get('stage', '')}",
                f"年级：{lesson_input.get('grade', '')}",
                f"学科：{lesson_input.get('subject', '')}",
                f"教材：{lesson_input.get('textbook_version', '')}",
                f"单元：{lesson_input.get('unit', '')}",
                f"课题：{lesson_input.get('lesson_title', '')}",
                f"课时：{lesson_input.get('duration_minutes', '')} 分钟",
            ],
        ),
        ("一、课标解读 · 核心素养", _as_list(curriculum.get("core_competencies"))),
        ("一、课标解读 · 学业要求", _as_list(curriculum.get("academic_requirements"))),
        ("一、课标解读 · 内容要点", _as_list(curriculum.get("content_points"))),
        ("一、课标解读 · 教学提示", _as_list(curriculum.get("teaching_tips_from_standard"))),
        ("二、教案 · 教学目标", _as_list(plan.get("teaching_objectives"))),
        ("二、教案 · 重点", _as_list(plan.get("key_points"))),
        ("二、教案 · 难点", _as_list(plan.get("difficult_points"))),
        ("二、教案 · 练习意图", _as_list(plan.get("practice_intents"))),
    ]

    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    stage_lines: list[str] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_lines.append(
            f"【{stage.get('name', '环节')}】{stage.get('duration_minutes', '')}分钟 | "
            f"师：{stage.get('teacher_activity', '')} | "
            f"生：{stage.get('student_activity', '')} | "
            f"目的：{stage.get('purpose', '')}"
        )
    sections.append(("二、教案 · 环节设计", stage_lines))

    pages = slides.get("pages") if isinstance(slides.get("pages"), list) else []
    page_lines: list[str] = []
    if slides.get("design_notes"):
        page_lines.append(f"设计说明：{slides.get('design_notes')}")
    for page in pages:
        if not isinstance(page, dict):
            continue
        bullets = "；".join(_as_list(page.get("bullets")))
        page_lines.append(f"P{page.get('index', '')} {page.get('title', '')}：{bullets}")
    sections.append(("三、课件大纲", page_lines))

    board_lines = [f"布局：{board.get('layout', 'main_side')}"]
    main_board = board.get("main_board") if isinstance(board.get("main_board"), list) else []
    for item in sorted(main_board, key=lambda x: int((x or {}).get("order") or 0) if isinstance(x, dict) else 0):
        if isinstance(item, dict):
            board_lines.append(f"主板书：{item.get('text', '')}")
    side_board = board.get("side_board") if isinstance(board.get("side_board"), list) else []
    for item in side_board:
        if isinstance(item, dict):
            board_lines.append(f"副板书：{item.get('text', '')}")
    board_lines.extend([f"书写顺序：{x}" for x in _as_list(board.get("writing_sequence"))])
    board_lines.extend([f"关键句：{x}" for x in _as_list(board.get("key_sentences"))])
    sections.append(("四、板书设计", board_lines))
    return sections


def export_docx(lesson_input: dict[str, Any], result: dict[str, Any]) -> bytes:
    from docx import Document

    doc = Document()
    title = f"{lesson_input.get('grade', '')} {lesson_input.get('lesson_title', '备课结果')}".strip()
    doc.add_heading(title or "备课结果", level=0)
    doc.add_paragraph("智能备课教研工作台 · 可编辑导出稿")

    for heading, lines in _collect_sections(lesson_input, result):
        if not lines:
            continue
        doc.add_heading(heading, level=1)
        for line in lines:
            doc.add_paragraph(line, style="List Bullet")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def export_pdf(lesson_input: dict[str, Any], result: dict[str, Any]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font_name = _register_cn_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CNTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=18,
        leading=24,
        spaceAfter=12,
    )
    h_style = ParagraphStyle(
        "CNH",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=13,
        leading=18,
        spaceBefore=10,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "CNBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10.5,
        leading=16,
        spaceAfter=3,
    )

    story = []
    title = f"{lesson_input.get('grade', '')} {lesson_input.get('lesson_title', '备课结果')}".strip()
    story.append(Paragraph(_escape(title or "备课结果"), title_style))
    story.append(Paragraph(_escape("智能备课教研工作台 · 可编辑导出稿"), body_style))
    story.append(Spacer(1, 8))

    for heading, lines in _collect_sections(lesson_input, result):
        if not lines:
            continue
        story.append(Paragraph(_escape(heading), h_style))
        for line in lines:
            story.append(Paragraph(_escape(f"• {line}"), body_style))

    doc.build(story)
    return buf.getvalue()


def export_pptx(lesson_input: dict[str, Any], result: dict[str, Any]) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    title = str(lesson_input.get("lesson_title") or "课件大纲")
    grade = str(lesson_input.get("grade") or "")
    _add_title_slide(prs, title, f"{grade} · 智能备课导出大纲")

    slides = result.get("slides") or {}
    pages = slides.get("pages") if isinstance(slides.get("pages"), list) else []
    if pages:
        for page in pages:
            if not isinstance(page, dict):
                continue
            bullets = _as_list(page.get("bullets"))
            if page.get("interaction"):
                bullets.append(f"互动：{page.get('interaction')}")
            keywords = _as_list(page.get("visual_keywords"))
            if keywords:
                bullets.append("配图关键词：" + "、".join(keywords))
            _add_bullet_slide(
                prs,
                f"P{page.get('index', '')} {page.get('title', '')}".strip(),
                bullets or ["（本页暂无要点）"],
            )
    else:
        # 没有课件时，用教案环节生成大纲页
        plan = result.get("lesson_plan") or {}
        stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            _add_bullet_slide(
                prs,
                str(stage.get("name") or "环节"),
                [
                    f"时长：{stage.get('duration_minutes', '')} 分钟",
                    f"教师活动：{stage.get('teacher_activity', '')}",
                    f"学生活动：{stage.get('student_activity', '')}",
                    f"目的：{stage.get('purpose', '')}",
                ],
            )

    board = result.get("blackboard") or {}
    board_bullets = [f"布局：{board.get('layout', '')}"]
    board_bullets.extend([f"主板书：{x.get('text')}" for x in (board.get("main_board") or []) if isinstance(x, dict)])
    board_bullets.extend([f"副板书：{x.get('text')}" for x in (board.get("side_board") or []) if isinstance(x, dict)])
    if len(board_bullets) > 1:
        _add_bullet_slide(prs, "板书提示", board_bullets)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _add_title_slide(prs, title: str, subtitle: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    if slide.placeholders and len(slide.placeholders) > 1:
        slide.placeholders[1].text = subtitle


def _add_bullet_slide(prs, title: str, bullets: list[str]) -> None:
    from pptx.util import Pt

    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    body = slide.shapes.placeholders[1].text_frame
    body.clear()
    for i, line in enumerate(bullets):
        p = body.paragraphs[0] if i == 0 else body.add_paragraph()
        p.text = line
        p.level = 0
        p.font.size = Pt(18)


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _register_cn_font() -> str:
    """注册系统中文字体，保证 PDF 中文可显示。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("SimSun", r"C:\Windows\Fonts\simsun.ttc"),
        ("SimHei", r"C:\Windows\Fonts\simhei.ttf"),
        ("MicrosoftYaHei", r"C:\Windows\Fonts\msyh.ttc"),
        ("NotoSansSC", r"C:\Windows\Fonts\NotoSansSC-Regular.otf"),
    ]
    for name, path in candidates:
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            return name
        except Exception:  # noqa: BLE001
            continue
    return "Helvetica"
