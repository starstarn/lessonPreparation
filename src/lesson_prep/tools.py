"""可供 Agent 按需调用的工具。"""

from __future__ import annotations

from langchain_core.tools import tool

from lesson_prep.logutil import safe_log
from lesson_prep.media_assets import generate_diagram as _generate_diagram
from lesson_prep.media_assets import search_images as _search_images
from lesson_prep.rag import retrieve_curriculum_context


@tool
def search_curriculum(query: str, k: int = 4) -> str:
    """检索《义务教育数学课程标准》相关文本片段。

    按需多次调用：可分别检索「核心素养」「学业要求」「内容要求」「教学提示」等侧面。
    查询中应包含年级、课时主题与目标侧面关键词，以便命中更准。

    Args:
        query: 检索查询，例如「七年级 有理数加减法 学业要求」。
        k: 返回片段数量，默认 4，建议 3～6。
    """
    k = max(1, min(int(k or 4), 8))
    safe_log(f"  [tool] search_curriculum(query={query!r}, k={k})")
    context, _docs = retrieve_curriculum_context(query, k=k)
    if not context.strip():
        return "（未检索到相关课标片段，请换关键词再试）"
    return context


@tool
def search_images(query: str, limit: int = 1) -> str:
    """搜索课堂配图并下载到本地。

    优先 Wikimedia / Openverse / Unsplash；外网不可用时自动生成本地教学示意图。

    Args:
        query: 英文或中文检索词，如「number line math」「七年级 数轴 示意图」。
        limit: 最多尝试候选数，默认 1。
    """
    return _search_images(query, limit=limit)


@tool
def generate_diagram(prompt: str) -> str:
    """生成简易教学示意图（数轴、坐标系、流程图等）。

    适合概念页、方法总结页；返回 JSON 含 media_id 与 preview_url。

    Args:
        prompt: 示意图描述，如「有理数加减法解题流程：审题→定符号→计算→检验」。
    """
    return _generate_diagram(prompt)
