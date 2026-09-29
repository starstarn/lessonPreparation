from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from lesson_prep.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CURRICULUM_META,
    DOC_DIR,
    MOCK_LLM,
    RETRIEVE_K,
    VECTORSTORE_DIR,
    find_curriculum_pdf,
)
from lesson_prep.llm import get_embeddings

KNOWLEDGE_DIR = DOC_DIR / "knowledge"

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_STAGE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("第一学段", ("第一学段", "一年级", "二年级")),
    ("第二学段", ("第二学段", "三年级", "四年级")),
    ("第三学段", ("第三学段", "五年级", "六年级")),
    ("第四学段", ("第四学段", "七年级", "八年级", "九年级", "初中")),
)
_DOMAINS = ("数与代数", "图形与几何", "统计与概率", "综合与实践")
_TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("有理数的加法", ("数与代数", "有理数的加法")),
    ("有理数", ("数与代数", "有理数")),
    ("方程", ("数与代数", "方程")),
    ("函数", ("数与代数", "函数")),
    ("三角形", ("图形与几何", "三角形")),
    ("几何", ("图形与几何",)),
    ("统计", ("统计与概率",)),
    ("概率", ("统计与概率",)),
)
_ASPECTS = (
    "内容要求",
    "学业要求",
    "教学提示",
    "数学抽象",
    "运算能力",
    "模型观念",
    "核心素养",
    "总目标",
    "教学建议",
)
_PARENTS = ("课程目标", "内容标准", "课程实施")
_LEVEL_NAME = {"root": "顶层", "parent": "父切片", "child": "子切片"}


def _splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "；", " ", ""],
    )


def _chunk_id(path: str, part: int, body: str) -> str:
    raw = f"{path}|{part}|{body[:80]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _infer_stage(text: str) -> str:
    for label, aliases in _STAGE_ALIASES:
        if any(alias in text for alias in aliases):
            return label
    return ""


def suggest_curriculum_path(
    *,
    grade: str = "",
    stage: str = "",
    unit: str = "",
    lesson_title: str = "",
    aspect: str = "",
) -> str:
    """按课时信息拼一条标签路径，供检索先做路径过滤。"""
    blob = f"{stage}{grade}{unit}{lesson_title}{aspect}"
    if any(key in aspect for key in ("核心素养", "数学抽象", "运算能力", "模型观念", "总目标")):
        tail = "核心素养"
        if "抽象" in aspect:
            tail = "核心素养-数学抽象"
        elif "运算" in aspect:
            tail = "核心素养-运算能力"
        elif "模型" in aspect:
            tail = "核心素养-模型观念"
        elif "总目标" in aspect:
            tail = "总目标"
        return f"课程目标-{tail}"
    if "教学建议" in aspect or "课程实施" in aspect:
        return "课程实施-教学建议"
    parts = ["内容标准"]
    band = _infer_stage(blob)
    if band:
        parts.append(band)
    for key, extras in _TOPIC_RULES:
        if key in blob:
            parts.extend(extras)
            break
    for name in ("内容要求", "学业要求", "教学提示"):
        if name in aspect:
            parts.append(name)
            break
    return "-".join(parts)


def _compose_page(path: str, parent_path: str, body: str) -> str:
    """标签路径与正文拼在一起，作为向量化文本。"""
    return f"路径：{path}\n父切片：{parent_path}\n{body.strip()}"


def _make_docs(
    *,
    path: str,
    parent_path: str,
    slice_level: str,
    stage_band: str,
    chapter: str,
    body: str,
    source_name: str,
    source_kind: str,
    extra: dict | None = None,
) -> list[Document]:
    text = body.strip()
    if not text:
        return []
    pieces = [text]
    if len(text) > CHUNK_SIZE * 2:
        pieces = [p.strip() for p in _splitter().split_text(text) if p.strip()]
    docs: list[Document] = []
    for part, piece in enumerate(pieces):
        meta = {
            **CURRICULUM_META,
            **(extra or {}),
            "source_file": source_name,
            "source_kind": source_kind,
            "label_path": path,
            "parent_path": parent_path,
            "slice_level": slice_level,
            "stage_band": stage_band,
            "chapter": chapter,
            "body": piece,
            "part": part,
            "chunk_id": _chunk_id(path, part, piece),
        }
        docs.append(Document(page_content=_compose_page(path, parent_path, piece), metadata=meta))
    return docs


def _iter_sections(text: str):
    level = 0
    title = ""
    buf: list[str] = []
    for line in text.splitlines():
        matched = _HEADING.match(line)
        if matched:
            if level:
                yield level, title, "\n".join(buf).strip()
            level = len(matched.group(1))
            title = re.sub(r"[*_`]", "", matched.group(2)).strip()
            buf = []
        else:
            buf.append(line)
    if level:
        yield level, title, "\n".join(buf).strip()


def _slice_level(level: int) -> str:
    if level <= 1:
        return "root"
    if level == 2:
        return "parent"
    return "child"


def _parent_path(titles: list[str], level: int) -> str:
    if level <= 1:
        return titles[0]
    if level == 2:
        return titles[0]
    return "-".join(titles[:2])


def chunk_curriculum_markdown(text: str, source_name: str) -> list[Document]:
    """按标题层级切成带完整路径的切片。

    顶层是课标基础信息，一级标题为父切片（课程目标、内容标准等），
    再往下是学段、章节、知识点要求，路径用「-」连成完整标签。
    """
    docs: list[Document] = []
    stack: list[tuple[int, str]] = []
    for level, title, body in _iter_sections(text):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        titles = [item[1] for item in stack]
        path = "-".join(titles)
        stage_band = next((item for item in titles if item.endswith("学段")), "")
        chapter = next((item for item in titles if item in _DOMAINS), "")
        docs.extend(
            _make_docs(
                path=path,
                parent_path=_parent_path(titles, level),
                slice_level=_slice_level(level),
                stage_band=stage_band,
                chapter=chapter,
                body=body,
                source_name=source_name,
                source_kind="markdown",
            )
        )
    return docs


def _plain_chunks(text: str, source_name: str, source_kind: str) -> list[Document]:
    path = "课标基础信息-原文摘录"
    return _make_docs(
        path=path,
        parent_path="课标基础信息",
        slice_level="child",
        stage_band="",
        chapter="",
        body=text,
        source_name=source_name,
        source_kind=source_kind,
    )


def load_knowledge_documents() -> list[Document]:
    """Load markdown/text knowledge files (preferred when PDF is scanned)."""
    if not KNOWLEDGE_DIR.exists():
        return []

    docs: list[Document] = []
    paths = sorted(
        list(KNOWLEDGE_DIR.glob("*.md"))
        + list(KNOWLEDGE_DIR.glob("*.txt"))
        + list(KNOWLEDGE_DIR.glob("*.markdown"))
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".md", ".markdown"}:
            docs.extend(chunk_curriculum_markdown(text, path.name))
        else:
            docs.extend(_plain_chunks(text, path.name, "ocr"))
    return docs


def load_pdf_documents(pdf_path: Path | None = None) -> list[Document]:
    pdf_path = pdf_path or find_curriculum_pdf()
    loader = PyPDFLoader(str(pdf_path))
    pages = loader.load()
    pages = [p for p in pages if (p.page_content or "").strip()]
    docs: list[Document] = []
    for page in pages:
        extra = {}
        if page.metadata.get("page") is not None:
            extra["page"] = page.metadata.get("page")
        docs.extend(
            _make_docs(
                path="课标基础信息-原文摘录",
                parent_path="课标基础信息",
                slice_level="child",
                stage_band="",
                chapter="",
                body=page.page_content,
                source_name=pdf_path.name,
                source_kind="pdf",
                extra=extra,
            )
        )
    return docs


@lru_cache(maxsize=1)
def load_all_documents() -> list[Document]:
    docs = load_knowledge_documents() + load_pdf_documents()
    if not docs:
        raise RuntimeError(
            "未能加载任何课标文本。你的 PDF 可能是扫描件。"
            "请使用 doc/knowledge/ 下的 Markdown，或运行 scripts/ocr_pdf.py 生成 OCR 文本。"
        )
    return docs


def build_vectorstore(force: bool = False) -> FAISS:
    VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)
    index_file = VECTORSTORE_DIR / "index.faiss"

    if index_file.exists() and not force:
        return FAISS.load_local(
            str(VECTORSTORE_DIR),
            get_embeddings(),
            allow_dangerous_deserialization=True,
        )

    if MOCK_LLM:
        raise RuntimeError("MOCK_LLM 模式下无法构建真实向量库，请先关闭 MOCK 或仅用 preview")

    load_all_documents.cache_clear()
    docs = load_all_documents()
    store = FAISS.from_documents(docs, get_embeddings())
    store.save_local(str(VECTORSTORE_DIR))

    meta_path = VECTORSTORE_DIR / "build_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "chunk_count": len(docs),
                "embed": "label_path+parent_path+body",
                "sources": sorted({d.metadata.get("source_file") for d in docs}),
                "label_paths": sorted({d.metadata.get("label_path") for d in docs if d.metadata.get("source_kind") == "markdown"}),
                **CURRICULUM_META,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return store


def get_retriever(k: int = RETRIEVE_K):
    store = build_vectorstore(force=False)
    return store.as_retriever(search_kwargs={"k": k})


def _path_segments(path: str) -> list[str]:
    return [part.strip() for part in re.split(r"[-－/／>＞]+", path or "") if part.strip() and part.strip() != "课标基础信息"]


def _required_segments(query: str, path: str) -> tuple[list[str], list[str]]:
    """返回 (硬性路径段, 侧面词)。侧面词之间是「或」，硬性段必须同时命中。"""
    blob = f"{query}\n{path}"
    hard: list[str] = []

    def add(item: str) -> None:
        if item and item not in hard and item != "课标基础信息":
            hard.append(item)

    for seg in _path_segments(path):
        add(seg)
    stage = _infer_stage(blob)
    if stage:
        add(stage)
    for key, extras in _TOPIC_RULES:
        if key in blob:
            for extra in extras:
                add(extra)
            break
    else:
        for domain in _DOMAINS:
            if domain in blob:
                add(domain)
                break
    aspects = [name for name in _ASPECTS if name in blob and name not in hard]
    parents = [name for name in _PARENTS if name in blob and name not in hard]
    if len(parents) == 1:
        add(parents[0])
    elif not parents:
        if any(name in hard or name in aspects for name in ("核心素养", "数学抽象", "运算能力", "模型观念", "总目标")):
            if "课程目标" not in hard:
                add("课程目标")
        elif any(name in hard or name in aspects for name in ("内容要求", "学业要求", "教学提示")):
            if "内容标准" not in hard:
                add("内容标准")
        elif "教学建议" in aspects or "教学建议" in hard:
            if "课程实施" not in hard:
                add("课程实施")
    return hard, aspects


def _filter_by_path(docs: list[Document], hard: list[str], aspects: list[str]) -> list[Document]:
    structured = [doc for doc in docs if doc.metadata.get("source_kind") == "markdown"]
    pool = structured or list(docs)
    current = list(hard)
    use_aspects = list(aspects)
    while True:
        matched: list[Document] = []
        for doc in pool:
            label = str(doc.metadata.get("label_path") or "")
            if not all(seg in label for seg in current):
                continue
            if use_aspects and not any(aspect in label for aspect in use_aspects):
                continue
            matched.append(doc)
        if matched:
            return matched
        if use_aspects:
            use_aspects = []
            continue
        if not current:
            return pool
        current = current[:-1]


def _query_tokens(query: str, path: str) -> list[str]:
    blob = f"{query} {path}"
    tokens = [part for part in re.split(r"[\s，。、；：,.\-－/／]+", blob) if len(part) >= 2]
    for label in (*_DOMAINS, *_ASPECTS, *_PARENTS, "有理数", "有理数的加法", "第四学段", "数学抽象"):
        if label in blob and label not in tokens:
            tokens.append(label)
    return tokens


def _keyword_score(query: str, path: str, doc: Document) -> int:
    tokens = _query_tokens(query, path)
    label = str(doc.metadata.get("label_path") or "")
    body = str(doc.metadata.get("body") or "")
    score = 0
    for token in tokens:
        if token in label:
            score += 5
        score += body.count(token)
    if doc.metadata.get("slice_level") == "child":
        score += 1
    return score


def _vector_scores(query: str, path: str, docs: list[Document]) -> dict[int, float] | None:
    """在已过滤的切片子集上算向量相似度；索引缺失或过期时返回 None。"""
    if MOCK_LLM:
        return None
    index_file = VECTORSTORE_DIR / "index.faiss"
    if not index_file.exists() or not docs:
        return None
    try:
        import numpy as np

        store = build_vectorstore(force=False)
        query_text = query if not path else f"路径：{path}\n{query}"
        query_vec = np.array(get_embeddings().embed_query(query_text), dtype="float32")
        id_to_index = {doc_id: idx for idx, doc_id in store.index_to_docstore_id.items()}
        by_chunk: dict[str, object] = {}
        for doc_id, stored in store.docstore._dict.items():
            chunk_id = (stored.metadata or {}).get("chunk_id")
            index = id_to_index.get(doc_id)
            if not chunk_id or index is None:
                continue
            by_chunk[str(chunk_id)] = np.array(store.index.reconstruct(int(index)), dtype="float32")
        scores: dict[int, float] = {}
        matched = 0
        for doc in docs:
            vector = by_chunk.get(str(doc.metadata.get("chunk_id")))
            if vector is None:
                continue
            matched += 1
            diff = vector - query_vec
            scores[id(doc)] = -float(np.dot(diff, diff))
        if matched < max(1, len(docs) // 2):
            return None
        return scores
    except Exception:
        return None


def _rank_within(query: str, path: str, docs: list[Document]) -> list[Document]:
    keyword = {id(doc): _keyword_score(query, path, doc) for doc in docs}
    vectors = _vector_scores(query, path, docs)
    if not vectors:
        return sorted(docs, key=lambda doc: keyword[id(doc)], reverse=True)

    def sort_key(doc: Document) -> tuple[float, int]:
        return (vectors.get(id(doc), -1e18), keyword[id(doc)])

    return sorted(docs, key=sort_key, reverse=True)


def _with_parent(ranked: list[Document], universe: list[Document], k: int) -> list[Document]:
    """子切片结果前补上父切片，方便确认整体方向。"""
    if not ranked or k <= 0:
        return []
    child = next((doc for doc in ranked if doc.metadata.get("slice_level") == "child"), ranked[0])
    parent_path = str(child.metadata.get("parent_path") or "")
    parent = next(
        (
            doc
            for doc in universe
            if doc.metadata.get("label_path") == parent_path and doc.metadata.get("slice_level") == "parent"
        ),
        None,
    )
    ordered: list[Document] = []
    seen: set[str] = set()
    for doc in ([parent] if parent else []) + ranked:
        if doc is None:
            continue
        chunk_id = str(doc.metadata.get("chunk_id"))
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        ordered.append(doc)
        if len(ordered) >= k:
            break
    return ordered


def retrieve_curriculum_context(
    query: str,
    k: int = RETRIEVE_K,
    path: str = "",
) -> tuple[str, list[Document]]:
    """混合检索：先按标签路径过滤，再在子集内做向量检索（无向量库时用关键词）。"""
    docs_all = list(load_all_documents())
    required, aspects = _required_segments(query, path)
    scoped = _filter_by_path(docs_all, required, aspects)
    ranked = _rank_within(query, path, scoped)
    picked = _with_parent(ranked, docs_all, k)
    return _format_docs(picked), picked


def _format_docs(docs: list[Document]) -> str:
    blocks: list[str] = []
    for i, doc in enumerate(docs, start=1):
        path = doc.metadata.get("label_path", "")
        parent = doc.metadata.get("parent_path", "")
        level = _LEVEL_NAME.get(str(doc.metadata.get("slice_level")), "切片")
        body = re.sub(r"\s+", " ", str(doc.metadata.get("body") or "")).strip()
        blocks.append(f"[片段{i} | {level} | 路径={path} | 父切片={parent}]\n{body}")
    return "\n\n".join(blocks)
