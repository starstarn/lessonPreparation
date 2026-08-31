from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty

from lesson_prep.config import MOCK_LLM, OUTPUT_DIR
from lesson_prep.graph import run_preparation
from lesson_prep.schemas import LearningProfile, LessonInput

console = Console()


def build_default_input(title: str) -> LessonInput:
    return LessonInput(
        stage="初中",
        subject="数学",
        textbook_version="人教版",
        grade="七年级",
        unit="有理数",
        lesson_title=title,
        duration_minutes=45,
        curriculum_year="2022",
        extra_notes="面向常规课，注重概念理解与基础应用",
        learning_profile=LearningProfile(
            class_level="average",
            prior_knowledge="已认识正负数与数轴",
            known_pain_points="符号法则容易混淆",
            focus="key_points",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="运行智能备课多 Agent 流水线")
    parser.add_argument("--title", default="有理数的加法", help="课时课题")
    parser.add_argument("--grade", default="七年级")
    parser.add_argument("--unit", default="")
    parser.add_argument(
        "--profile",
        default="full",
        help="Agent 场景：full / plan_only / homework / public_lesson / quick_draft / custom",
    )
    parser.add_argument(
        "--agents",
        default="",
        help="自定义 Agent 列表，逗号分隔，如 curriculum,lesson_plan,exercises",
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        help="可选：从 JSON 文件读取完整 LessonInput",
    )
    args = parser.parse_args()

    if args.input_json:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
        lesson = LessonInput.model_validate(payload)
    else:
        lesson = build_default_input(args.title)
        lesson.grade = args.grade
        if args.unit:
            lesson.unit = args.unit
        lesson.agent_profile = args.profile
        if args.agents.strip():
            lesson.agent_profile = "custom"
            lesson.enabled_agents = [a.strip() for a in args.agents.split(",") if a.strip()]

    mode = "MOCK" if MOCK_LLM else "LIVE"
    console.print(
        Panel(
            f"课题：{lesson.lesson_title}\n模式：{mode}\n场景：{lesson.agent_profile}",
            title="智能备课教研团队",
        )
    )

    result = run_preparation(lesson.model_dump(), pause_after_plan=False)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"prep_{stamp}.json"
    # 检索上下文可能很长，默认仍保存，便于调试
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    console.print("\n[bold cyan]0) 场景装配[/bold cyan]")
    console.print(Pretty(result.get("agent_plan")))
    console.print("\n[bold cyan]1) 课标解读[/bold cyan]")
    console.print(Pretty(result.get("curriculum_analysis")))
    console.print("\n[bold cyan]2) 教案[/bold cyan]")
    console.print(Pretty(result.get("lesson_plan")))
    console.print("\n[bold cyan]3) 课件大纲[/bold cyan]")
    console.print(Pretty(result.get("slides")))
    console.print("\n[bold cyan]4) 板书设计[/bold cyan]")
    console.print(Pretty(result.get("blackboard")))
    console.print(f"\n[green]已保存[/green] {out_path}")


if __name__ == "__main__":
    main()
