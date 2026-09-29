"""课标层级切片与混合检索：先路径过滤，再在子集内排序。"""

from __future__ import annotations

from lesson_prep.rag import (
    chunk_curriculum_markdown,
    retrieve_curriculum_context,
    suggest_curriculum_path,
)


def test_chunks_keep_full_path_and_embed_label_with_body():
    text = """# 课标基础信息

顶层只写课标范围。

## 课程目标

课程目标统领内容标准。

### 核心素养

三会。

#### 数学抽象

从数量关系中抽象出研究对象。
"""
    docs = chunk_curriculum_markdown(text, "sample.md")
    leaf = next(doc for doc in docs if doc.metadata["label_path"].endswith("数学抽象"))
    assert leaf.metadata["label_path"] == "课标基础信息-课程目标-核心素养-数学抽象"
    assert leaf.metadata["parent_path"] == "课标基础信息-课程目标"
    assert leaf.metadata["slice_level"] == "child"
    assert leaf.page_content.startswith("路径：课标基础信息-课程目标-核心素养-数学抽象")
    assert "从数量关系中抽象出研究对象" in leaf.page_content
    assert leaf.metadata["body"] == "从数量关系中抽象出研究对象。"


def test_path_filter_then_rank_stays_inside_chapter():
    context, docs = retrieve_curriculum_context(
        "异号两数相加 法则",
        k=4,
        path="内容标准-第四学段-数与代数-有理数的加法",
    )
    paths = [doc.metadata["label_path"] for doc in docs]
    assert paths[0] == "课标基础信息-内容标准"
    assert any("有理数的加法" in path for path in paths[1:])
    assert all("统计与概率" not in path for path in paths)
    assert "父切片=课标基础信息-内容标准" in context
    assert "子切片" in context


def test_competency_path_returns_child_and_parent_direction():
    _context, docs = retrieve_curriculum_context(
        "抽象能力",
        k=3,
        path="课程目标-核心素养-数学抽象",
    )
    paths = [doc.metadata["label_path"] for doc in docs]
    assert "课标基础信息-课程目标" in paths
    assert "课标基础信息-课程目标-核心素养-数学抽象" in paths
    assert all("有理数的加法" not in path for path in paths)


def test_suggest_path_from_grade_and_lesson():
    path = suggest_curriculum_path(
        grade="七年级",
        stage="初中",
        unit="有理数",
        lesson_title="有理数的加法",
        aspect="学业要求",
    )
    assert path == "内容标准-第四学段-数与代数-有理数的加法-学业要求"
