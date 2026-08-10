"""本地演示题库：关键词检索（可后续换成向量检索）。"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from lesson_prep.config import DOC_DIR, ROOT_DIR
from lesson_prep.logutil import safe_log

QUESTION_BANK_DIR = DOC_DIR / "question_bank"
DEMO_BANK_FILE = QUESTION_BANK_DIR / "math_junior_demo.json"


@lru_cache(maxsize=1)
def load_question_bank() -> list[dict[str, Any]]:
    path = DEMO_BANK_FILE
    if not path.exists():
        # 兼容从仓库根目录启动
        alt = ROOT_DIR / "doc" / "question_bank" / "math_junior_demo.json"
        path = alt if alt.exists() else path
    if not path.exists():
        safe_log(f"  题库文件不存在: {path}")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict) and x.get("id") and x.get("stem")]


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    parts = re.split(r"[\s,，、;；|/]+", text)
    tokens: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        tokens.append(p)
        # 中文连续字串再切成 2-gram，提高「有理数加法」类命中
        if re.search(r"[\u4e00-\u9fff]", p) and len(p) >= 2:
            tokens.extend(p[i : i + 2] for i in range(len(p) - 1))
    return tokens


def _score_item(item: dict[str, Any], query: str, grade: str, difficulty: str) -> float:
    blob = " ".join(
        [
            str(item.get("grade") or ""),
            str(item.get("unit") or ""),
            str(item.get("knowledge_point") or ""),
            str(item.get("stem") or ""),
            " ".join(item.get("tags") or []),
            str(item.get("question_type") or ""),
            str(item.get("difficulty") or ""),
        ]
    ).lower()
    score = 0.0
    for tok in _tokenize(query):
        if tok and tok in blob:
            score += 2.0 if len(tok) >= 2 else 1.0
    if grade and str(item.get("grade") or "") == grade:
        score += 3.0
    if difficulty and difficulty != "any" and str(item.get("difficulty") or "") == difficulty:
        score += 2.5
    # 课题词权重
    for key in ("有理数", "加法", "减法", "乘法", "数轴", "方程"):
        if key in query and key in blob:
            score += 1.5
    return score


def search_question_bank(
    query: str,
    *,
    grade: str = "",
    difficulty: str = "any",
    question_type: str = "any",
    k: int = 8,
) -> list[dict[str, Any]]:
    """按关键词检索题库，返回最多 k 道题（含 id，便于组卷引用）。"""
    items = load_question_bank()
    if not items:
        return []

    q = (query or "").strip()
    grade = (grade or "").strip()
    difficulty = (difficulty or "any").strip().lower()
    question_type = (question_type or "any").strip().lower()
    k = max(1, min(int(k or 8), 15))

    scored: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        if question_type != "any" and str(item.get("question_type") or "") != question_type:
            continue
        if difficulty != "any" and str(item.get("difficulty") or "") != difficulty:
            # 不硬过滤，降权即可；若完全不匹配 query 后面会被刷掉
            pass
        s = _score_item(item, q, grade, difficulty)
        if s > 0:
            scored.append((s, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = [dict(it) for _, it in scored[:k]]

    # 若几乎没命中，按年级兜底取若干
    if not chosen and grade:
        chosen = [dict(it) for it in items if str(it.get("grade") or "") == grade][:k]
    if not chosen:
        chosen = [dict(it) for it in items[:k]]
    return chosen


def format_question_hits(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "（题库无命中，可换关键词或放宽 difficulty）"
    blocks: list[str] = []
    for i, item in enumerate(hits, start=1):
        opts = item.get("options") or []
        opt_line = "；".join(str(o) for o in opts) if opts else ""
        blocks.append(
            "\n".join(
                [
                    f"[{i}] id={item.get('id')} | {item.get('difficulty')}/{item.get('question_type')} | "
                    f"{item.get('score')}分 | {item.get('knowledge_point')}",
                    f"题干：{item.get('stem')}",
                    f"选项：{opt_line}" if opt_line else "选项：（无）",
                    f"答案：{item.get('answer')}",
                    f"解析：{item.get('analysis')}",
                    f"年级/单元：{item.get('grade')} / {item.get('unit')}",
                ]
            )
        )
    return "\n\n".join(blocks)
