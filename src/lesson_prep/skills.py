"""Agent Skill 注册表：比插件更细的可复用能力包。

Profile → Agent 插件 → Skills → Tools / 提示约束 / 规则

当前内置：
- shared.read_anchors：材料 Agent 生成前读取教学三元组硬约束
- consistency.anchors：一致性检查对照三元组覆盖与难度
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from lesson_prep.logutil import safe_log
from lesson_prep.schemas import (
    Blackboard,
    ExercisePaper,
    LessonInput,
    LessonPlan,
    Slides,
    TeachingAnchor,
)

SkillKind = Literal["prompt", "rule", "shared"]


@dataclass(frozen=True)
class AgentSkill:
    """可挂到多个 Agent 上的能力包。"""

    id: str
    label: str
    description: str
    kind: SkillKind
    # 默认挂到哪些 Agent；空表示由调用方显式引用
    default_agents: tuple[str, ...] = ()


SKILL_REGISTRY: dict[str, AgentSkill] = {
    "shared.read_anchors": AgentSkill(
        id="shared.read_anchors",
        label="读取教学三元组",
        description="材料生成前先读共享状态中的「教学目标-知识点-难度」，注入提示词硬约束",
        kind="shared",
        default_agents=("exercises", "slides", "blackboard"),
    ),
    "consistency.anchors": AgentSkill(
        id="consistency.anchors",
        label="对照三元组质检",
        description="一致性检查时核对各材料是否覆盖三元组知识点，难度是否越级",
        kind="rule",
        default_agents=("consistency",),
    ),
}

_DIFFICULTY_LABEL = {
    "basic": "基础",
    "intermediate": "进阶",
    "advanced": "拓展",
}
_DIFFICULTY_HINT = {
    "basic": "只做识记与直接应用，不出综合压轴",
    "intermediate": "围绕重难点做变式，难度中等",
    "advanced": "可做综合或拓展，但不得超纲到三元组之外",
}
BANK_DIFFICULTY = {
    "basic": "easy",
    "intermediate": "medium",
    "advanced": "hard",
}


def list_skills() -> list[AgentSkill]:
    return list(SKILL_REGISTRY.values())


def get_skill(skill_id: str) -> AgentSkill | None:
    return SKILL_REGISTRY.get(skill_id)


def skills_for_agent(agent_id: str) -> list[AgentSkill]:
    return [s for s in SKILL_REGISTRY.values() if agent_id in s.default_agents]


def skills_public() -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "label": s.label,
            "description": s.description,
            "kind": s.kind,
            "default_agents": list(s.default_agents),
        }
        for s in list_skills()
    ]


def agent_has_skill(agent_id: str, skill_id: str) -> bool:
    skill = SKILL_REGISTRY.get(skill_id)
    return bool(skill and agent_id in skill.default_agents)


# ---- shared.read_anchors -------------------------------------------------


def _text_related(a: str, b: str) -> bool:
    left, right = (a or "").strip(), (b or "").strip()
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    if len(left) >= 4 and len(right) >= 4:
        return any(left[i : i + 4] in right for i in range(len(left) - 3))
    return False


def _best_objective(point: str, objectives: list[str], index: int) -> str:
    best = objectives[index % len(objectives)]
    best_score = 0
    for obj in objectives:
        score = 0
        if point and point in obj:
            score += 5
        if len(point) >= 2:
            grams = {point[i : i + 2] for i in range(len(point) - 1)}
            score += sum(1 for gram in grams if gram in obj)
        if score > best_score:
            best_score = score
            best = obj
    return best


def extract_teaching_anchors(
    plan: LessonPlan,
    lesson: LessonInput | None = None,
) -> list[TeachingAnchor]:
    """从定稿教案抽出「教学目标-知识点-难度层级」，最多 6 条。"""
    focus = "key_points"
    title = ""
    if lesson is not None:
        focus = lesson.learning_profile.focus or "key_points"
        title = (lesson.lesson_title or "").strip()

    objectives = [x.strip() for x in (plan.teaching_objectives or []) if (x or "").strip()]
    points = [x.strip() for x in (plan.key_points or []) if (x or "").strip()]
    hard = [x.strip() for x in (plan.difficult_points or []) if (x or "").strip()]
    if not points:
        points = [title or "本节核心内容"]
    if not objectives:
        objectives = [f"理解并运用：{points[0]}"]

    used_hard: set[int] = set()

    def level_for(point: str, index: int) -> str:
        matched = False
        for i, item in enumerate(hard):
            if _text_related(point, item):
                used_hard.add(i)
                matched = True
                break
        if focus == "foundation":
            return "intermediate" if matched else "basic"
        if focus == "extension":
            return "advanced" if matched or index > 0 else "intermediate"
        if matched:
            return "advanced"
        return "basic" if index == 0 else "intermediate"

    anchors: list[TeachingAnchor] = []
    for i, point in enumerate(points):
        if len(anchors) >= 6:
            break
        anchors.append(
            TeachingAnchor(
                objective=_best_objective(point, objectives, i),
                knowledge_point=point,
                difficulty=level_for(point, i),  # type: ignore[arg-type]
            )
        )
    for i, item in enumerate(hard):
        if len(anchors) >= 6:
            break
        if i in used_hard or any(_text_related(item, a.knowledge_point) for a in anchors):
            continue
        anchors.append(
            TeachingAnchor(
                objective=_best_objective(item, objectives, len(anchors)),
                knowledge_point=item,
                difficulty="intermediate" if focus == "foundation" else "advanced",
            )
        )
    return anchors


def format_anchor_constraint(anchors: list[TeachingAnchor]) -> str:
    """写成下游 Agent 必须先读的硬约束文本（shared.read_anchors）。"""
    if not anchors:
        return (
            "【硬约束·shared.read_anchors】未提取到教学三元组。请严格依据教案重点生成，"
            "不要另起知识点，也不要整体拔高或降低难度。"
        )
    lines = [
        "【硬约束·shared.read_anchors】以下「教学目标-知识点-难度层级」三元组"
        "提取自已定稿教案，并已写入共享状态。",
        "生成前必须先逐条阅读。课件、习题、板书只能覆盖这些知识点，难度不得偏离标注层级。",
    ]
    for i, anchor in enumerate(anchors, start=1):
        label = _DIFFICULTY_LABEL.get(anchor.difficulty, anchor.difficulty)
        hint = _DIFFICULTY_HINT.get(anchor.difficulty, "")
        lines.append(
            f"{i}. 教学目标：{anchor.objective}｜知识点：{anchor.knowledge_point}"
            f"｜难度层级：{label}（{hint}）"
        )
    lines.append("禁止引入上述列表之外的新知识点；每条知识点至少出现一次。")
    return "\n".join(lines)


def resolve_teaching_anchors(
    plan: LessonPlan,
    lesson: LessonInput,
    anchors: list[TeachingAnchor] | None = None,
) -> list[TeachingAnchor]:
    """优先使用共享状态里的三元组；缺失时才按当前教案现提。"""
    if anchors:
        return anchors
    safe_log("[skill:shared.read_anchors] 未传入共享状态，按当前教案现提")
    return extract_teaching_anchors(plan, lesson)


def apply_read_anchors(
    system: str,
    user: str,
    anchors: list[TeachingAnchor],
) -> tuple[str, str]:
    """shared.read_anchors：把三元组硬约束注入生成提示。"""
    system = system.rstrip() + (
        "\n硬约束（skill:shared.read_anchors）：必须先遵守用户消息开头的"
        "「教学目标-知识点-难度层级」三元组，不得新增知识点，不得偏离标注的难度层级。"
    )
    return system, format_anchor_constraint(anchors) + "\n\n" + user


# ---- consistency.anchors -------------------------------------------------


def _anchor_in_blob(point: str, blob: str) -> bool:
    """知识点是否出现在材料中：整词优先，其次连续 4 字，避免「同号/异号」误命中。"""
    point = (point or "").strip()
    if not point:
        return True
    if point in blob:
        return True
    if len(point) >= 4 and any(point[i : i + 4] in blob for i in range(len(point) - 3)):
        return True
    return False


def _exercise_blob(paper: ExercisePaper) -> str:
    return " ".join(paper.knowledge_coverage or []) + " " + " ".join(
        f"{it.knowledge_point} {it.stem}" for it in (paper.items or [])
    )


def _slides_blob(slides: Slides) -> str:
    return " ".join(
        f"{p.title} {' '.join(p.bullets or [])} {p.linked_stage}"
        for p in (slides.pages or [])
    )


def _board_blob(board: Blackboard) -> str:
    return (
        " ".join(x.text for x in (board.main_board or []))
        + " "
        + " ".join(x.text for x in (board.side_board or []))
        + " "
        + " ".join(board.key_sentences or [])
        + " "
        + " ".join(board.linked_stages or [])
    )


def check_anchors_consistency(
    anchors: list[TeachingAnchor],
    paper: ExercisePaper,
    slides: Slides,
    board: Blackboard,
    *,
    active_modules: set[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """consistency.anchors：对照三元组检查覆盖与难度。

    返回 (issues, fixes, conflict_modules)。
    """
    check = active_modules or {"exercises", "slides", "blackboard"}
    if not anchors:
        return [], [], []

    issues: list[str] = []
    fixes: list[str] = []
    conflicts: set[str] = set()

    blobs: dict[str, str] = {}
    if "exercises" in check:
        blobs["exercises"] = _exercise_blob(paper)
    if "slides" in check:
        blobs["slides"] = _slides_blob(slides)
    if "blackboard" in check:
        blobs["blackboard"] = _board_blob(board)

    # 每条知识点：在已启用材料中至少出现一次（允许某一路缺失，但整体不能全缺）
    for anchor in anchors:
        point = anchor.knowledge_point
        hit_modules = [mod for mod, blob in blobs.items() if _anchor_in_blob(point, blob)]
        if blobs and not hit_modules:
            issues.append(f"三元组知识点「{point}」未出现在已生成材料中")
            fixes.append("相关设计师：按 shared.read_anchors 覆盖该知识点")
            conflicts.update(blobs.keys())

    # 习题：各知识点应在卷内出现；难度不得整体越级
    if "exercises" in check and (paper.items or []):
        ex_blob = blobs.get("exercises", "")
        missed = [
            a.knowledge_point
            for a in anchors
            if a.knowledge_point and not _anchor_in_blob(a.knowledge_point, ex_blob)
        ]
        if missed and len(missed) >= max(1, (len(anchors) + 1) // 2):
            issues.append(f"习题未覆盖三元组知识点：{'、'.join(missed[:4])}")
            fixes.append("习题组卷师：按 teaching_anchors 补题并写入 knowledge_coverage")
            conflicts.add("exercises")

        hard_n = sum(1 for it in paper.items if it.difficulty == "hard")
        easy_n = sum(1 for it in paper.items if it.difficulty == "easy")
        has_basic = any(a.difficulty == "basic" for a in anchors)
        has_advanced = any(a.difficulty == "advanced" for a in anchors)
        only_basic = anchors and all(a.difficulty == "basic" for a in anchors)
        if only_basic and hard_n > easy_n and len(paper.items) >= 3:
            issues.append("三元组均为基础层级，但习题难题多于易题")
            fixes.append("习题组卷师：减少 hard，增加 easy/medium")
            conflicts.add("exercises")
        if has_advanced and hard_n == 0 and len(paper.items) >= 4:
            issues.append("三元组含拓展层级，但习题缺少难题")
            fixes.append("习题组卷师：至少增加 1～2 道 hard")
            conflicts.add("exercises")
        if has_basic and easy_n == 0 and len(paper.items) >= 4:
            issues.append("三元组含基础层级，但习题缺少易题")
            fixes.append("习题组卷师：增加 easy 基础巩固题")
            conflicts.add("exercises")

    # 课件 / 板书：整路完全未体现任何三元组知识点才算硬伤
    # （投影页未必逐条写出知识点原文，覆盖主要由习题 + 全局检查兜住）
    if "slides" in check and (slides.pages or []):
        hit_any = any(
            _anchor_in_blob(a.knowledge_point, blobs["slides"])
            for a in anchors
            if a.knowledge_point
        )
        if not hit_any:
            issues.append("课件未体现任何教学三元组知识点")
            fixes.append("课件生成师：在标题或 bullets 中写入至少一条三元组知识点")
            conflicts.add("slides")

    if "blackboard" in check and (board.main_board or board.key_sentences):
        hit_any = any(
            _anchor_in_blob(a.knowledge_point, blobs["blackboard"])
            for a in anchors
            if a.knowledge_point
        )
        if not hit_any:
            issues.append("板书未体现任何教学三元组知识点")
            fixes.append("板书设计师：在主板书或关键句中写入至少一条三元组知识点")
            conflicts.add("blackboard")

    return issues, fixes, sorted(conflicts)
