"""Agent 插件注册表 + 场景装配（可插拔编排）。

借鉴 DeepSeek Harness「能力与编排分离」思想：
- 每个 Agent 是可注册的插件（声明 id / 依赖 / 阶段）
- 场景 profile 只描述「启用哪些 Agent」，核心流水线按配置调度
- 新增材料类型时：注册插件 +（可选）加到某个 profile，无需改死流水线顺序
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from lesson_prep.config import DATA_DIR

AgentPhase = Literal["upstream", "core", "material", "qa"]


@dataclass(frozen=True)
class AgentPlugin:
    """单个可插拔 Agent 的元数据。"""

    id: str
    label: str
    description: str
    phase: AgentPhase
    depends_on: tuple[str, ...] = ()
    # 并行材料分路（仅 material 阶段）
    parallel: bool = False
    # 写入 PrepState 的主要产物键
    output_keys: tuple[str, ...] = ()
    # 是否允许用户在自定义场景中取消勾选
    user_toggleable: bool = True


@dataclass(frozen=True)
class AgentProfile:
    """场景模板：一组 Agent 的装配方案。"""

    id: str
    name: str
    description: str
    agents: tuple[str, ...]
    # 前端展示排序；越小越靠前
    order: int = 100


# 内置插件（能力层）。编排层只读注册表，不硬编码业务函数绑定。
AGENT_PLUGINS: dict[str, AgentPlugin] = {
    "curriculum": AgentPlugin(
        id="curriculum",
        label="课标解读员",
        description="检索课标并对齐核心素养、学业要求与内容要点",
        phase="upstream",
        output_keys=("curriculum_analysis", "retrieved_context"),
        user_toggleable=True,
    ),
    "lesson_plan": AgentPlugin(
        id="lesson_plan",
        label="教案设计师",
        description="基于课标解读撰写教学目标、环节与重难点",
        phase="core",
        depends_on=("curriculum",),
        output_keys=("lesson_plan", "lesson_plan_revise_count"),
        user_toggleable=True,
    ),
    "lesson_review": AgentPlugin(
        id="lesson_review",
        label="教案审核员",
        description="规则 + LLM 双质检，不通过可打回修改一次",
        phase="core",
        depends_on=("lesson_plan",),
        output_keys=("lesson_plan_qa",),
        user_toggleable=True,
    ),
    "exercises": AgentPlugin(
        id="exercises",
        label="习题组卷师",
        description="按教案重点生成练习卷",
        phase="material",
        depends_on=("lesson_plan",),
        parallel=True,
        output_keys=("exercise_paper", "exercise_qa"),
        user_toggleable=True,
    ),
    "slides": AgentPlugin(
        id="slides",
        label="课件生成师",
        description="按教案环节生成课件大纲与配图",
        phase="material",
        depends_on=("lesson_plan",),
        parallel=True,
        output_keys=("slides", "slides_qa"),
        user_toggleable=True,
    ),
    "blackboard": AgentPlugin(
        id="blackboard",
        label="板书设计师",
        description="设计主板书与副板书，对齐教案环节",
        phase="material",
        depends_on=("lesson_plan",),
        parallel=True,
        output_keys=("blackboard",),
        user_toggleable=True,
    ),
    "consistency": AgentPlugin(
        id="consistency",
        label="一致性检查员",
        description="检查已生成材料是否对齐同一教案",
        phase="qa",
        depends_on=("lesson_plan",),
        output_keys=("consistency_qa", "consistency_revise_count"),
        user_toggleable=True,
    ),
}

# 内置场景（与 data/agent_profiles.json 合并；文件可覆盖同名 id）
_BUILTIN_PROFILES: dict[str, AgentProfile] = {
    "full": AgentProfile(
        id="full",
        name="完整备课",
        description="课标 → 教案 ⇄ 审核 → 课件/习题/板书 → 一致性（默认全流程）",
        agents=(
            "curriculum",
            "lesson_plan",
            "lesson_review",
            "exercises",
            "slides",
            "blackboard",
            "consistency",
        ),
        order=10,
    ),
    "plan_only": AgentProfile(
        id="plan_only",
        name="仅教案",
        description="只要课标解读 + 教案撰写与审核，适合先定教学设计",
        agents=("curriculum", "lesson_plan", "lesson_review"),
        order=20,
    ),
    "homework": AgentProfile(
        id="homework",
        name="教案 + 习题",
        description="备课重点在练习设计，不生成课件与板书",
        agents=(
            "curriculum",
            "lesson_plan",
            "lesson_review",
            "exercises",
            "consistency",
        ),
        order=30,
    ),
    "public_lesson": AgentProfile(
        id="public_lesson",
        name="公开课套件",
        description="偏展示：教案 + 课件 + 板书，不做习题组卷",
        agents=(
            "curriculum",
            "lesson_plan",
            "lesson_review",
            "slides",
            "blackboard",
            "consistency",
        ),
        order=40,
    ),
    "materials_only": AgentProfile(
        id="materials_only",
        name="三件套材料",
        description="课标教案齐全后并行生成课件/习题/板书并做一致性检查",
        agents=(
            "curriculum",
            "lesson_plan",
            "lesson_review",
            "exercises",
            "slides",
            "blackboard",
            "consistency",
        ),
        order=50,
    ),
    "custom": AgentProfile(
        id="custom",
        name="自定义组合",
        description="自行勾选本次要调度的 Agent（依赖会自动补齐）",
        agents=(),
        order=90,
    ),
}


def list_plugins() -> list[AgentPlugin]:
    return list(AGENT_PLUGINS.values())


def get_plugin(agent_id: str) -> AgentPlugin | None:
    return AGENT_PLUGINS.get(agent_id)


def material_plugin_ids() -> list[str]:
    return [p.id for p in AGENT_PLUGINS.values() if p.phase == "material"]


def lane_labels() -> dict[str, str]:
    return {p.id: p.label for p in AGENT_PLUGINS.values() if p.parallel}


def _load_file_profiles() -> dict[str, AgentProfile]:
    path = DATA_DIR / "agent_profiles.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, AgentProfile] = {}
    items = raw.get("profiles") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return {}
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        agents = tuple(str(a) for a in (item.get("agents") or []) if a in AGENT_PLUGINS)
        out[str(item["id"])] = AgentProfile(
            id=str(item["id"]),
            name=str(item.get("name") or item["id"]),
            description=str(item.get("description") or ""),
            agents=agents,
            order=int(item.get("order") or 100),
        )
    return out


@lru_cache(maxsize=1)
def list_profiles() -> tuple[AgentProfile, ...]:
    merged = dict(_BUILTIN_PROFILES)
    merged.update(_load_file_profiles())
    return tuple(sorted(merged.values(), key=lambda p: (p.order, p.id)))


def get_profile(profile_id: str) -> AgentProfile:
    for p in list_profiles():
        if p.id == profile_id:
            return p
    return _BUILTIN_PROFILES["full"]


def ensure_dependencies(selected: list[str] | set[str]) -> list[str]:
    """按依赖闭包补齐上游 Agent，并保持稳定拓扑顺序。"""
    chosen = set(selected)
    changed = True
    while changed:
        changed = False
        for aid in list(chosen):
            plugin = AGENT_PLUGINS.get(aid)
            if not plugin:
                chosen.discard(aid)
                continue
            for dep in plugin.depends_on:
                if dep not in chosen and dep in AGENT_PLUGINS:
                    chosen.add(dep)
                    changed = True

    # 一致性：至少要有一路材料才有意义；否则自动去掉
    materials = {a for a in chosen if AGENT_PLUGINS.get(a) and AGENT_PLUGINS[a].phase == "material"}
    if "consistency" in chosen and not materials:
        chosen.discard("consistency")

    # 教案审核依赖教案；若无教案则去掉审核
    if "lesson_review" in chosen and "lesson_plan" not in chosen:
        chosen.discard("lesson_review")

    order = list(AGENT_PLUGINS.keys())
    return [a for a in order if a in chosen]


def resolve_enabled_agents(
    *,
    profile_id: str | None = None,
    enabled_agents: list[str] | None = None,
) -> list[str]:
    """解析本次运行启用的 Agent 列表。

    - 若传入 enabled_agents（非空），以其为准（自定义组合）
    - 否则使用 profile 预设
    - 最后做依赖补齐
    """
    profile_id = (profile_id or "full").strip() or "full"
    if enabled_agents:
        raw = [str(a).strip() for a in enabled_agents if str(a).strip()]
    else:
        profile = get_profile(profile_id)
        if profile.id == "custom":
            # 自定义但未勾选时回退完整流程，避免空跑
            raw = list(_BUILTIN_PROFILES["full"].agents)
        else:
            raw = list(profile.agents)
    return ensure_dependencies(raw)


@dataclass
class RunPlan:
    """一次备课的可执行装配计划。"""

    profile_id: str
    agents: list[str] = field(default_factory=list)

    @property
    def run_curriculum(self) -> bool:
        return "curriculum" in self.agents

    @property
    def run_lesson_plan(self) -> bool:
        return "lesson_plan" in self.agents

    @property
    def run_lesson_review(self) -> bool:
        return "lesson_review" in self.agents

    @property
    def material_agents(self) -> list[str]:
        return [a for a in self.agents if AGENT_PLUGINS[a].phase == "material"]

    @property
    def run_materials(self) -> bool:
        return bool(self.material_agents)

    @property
    def run_consistency(self) -> bool:
        return "consistency" in self.agents and self.run_materials

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "agents": list(self.agents),
            "material_agents": self.material_agents,
            "run_curriculum": self.run_curriculum,
            "run_lesson_plan": self.run_lesson_plan,
            "run_lesson_review": self.run_lesson_review,
            "run_materials": self.run_materials,
            "run_consistency": self.run_consistency,
        }


def build_run_plan(
    *,
    profile_id: str | None = None,
    enabled_agents: list[str] | None = None,
) -> RunPlan:
    pid = (profile_id or "full").strip() or "full"
    agents = resolve_enabled_agents(profile_id=pid, enabled_agents=enabled_agents)
    return RunPlan(profile_id=pid, agents=agents)


def plugins_public() -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "label": p.label,
            "description": p.description,
            "phase": p.phase,
            "depends_on": list(p.depends_on),
            "parallel": p.parallel,
            "user_toggleable": p.user_toggleable,
        }
        for p in list_plugins()
    ]


def profiles_public() -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "agents": list(p.agents),
            "order": p.order,
        }
        for p in list_profiles()
    ]
