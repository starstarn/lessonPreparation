from __future__ import annotations

import json
import re
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader, TextLoader
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


def _splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "；", " ", ""],
    )


def _attach_meta(docs: list[Document], source_name: str) -> list[Document]:
    enriched: list[Document] = []
    for doc in docs:
        meta = {
            **doc.metadata,
            **CURRICULUM_META,
            "source_file": source_name,
        }
        enriched.append(Document(page_content=doc.page_content, metadata=meta))
    return enriched


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
        # Prefer heading-aware splits for curriculum markdown
        if path.suffix.lower() in {".md", ".markdown"}:
            sections = re.split(r"(?m)(?=^## )", text)
            sections = [s.strip() for s in sections if s.strip()]
            for idx, section in enumerate(sections):
                docs.append(
                    Document(
                        page_content=section,
                        metadata={
                            **CURRICULUM_META,
                            "source_file": path.name,
                            "section_index": idx,
                        },
                    )
                )
        else:
            loader = TextLoader(str(path), encoding="utf-8")
            loaded = loader.load()
            docs.extend(_attach_meta(loaded, path.name))

    # Keep section docs as-is when already heading-split; otherwise chunk long texts
    final: list[Document] = []
    splitter = _splitter()
    for doc in docs:
        if len(doc.page_content) <= CHUNK_SIZE * 2:
            final.append(doc)
        else:
            final.extend(splitter.split_documents([doc]))
    return final


def load_pdf_documents(pdf_path: Path | None = None) -> list[Document]:
    pdf_path = pdf_path or find_curriculum_pdf()
    loader = PyPDFLoader(str(pdf_path))
    pages = loader.load()
    # Drop empty pages (common for scanned PDFs)
    pages = [p for p in pages if (p.page_content or "").strip()]
    if not pages:
        return []
    return _attach_meta(_splitter().split_documents(pages), pdf_path.name)


def load_all_documents() -> list[Document]:
    knowledge_docs = load_knowledge_documents()
    pdf_docs = load_pdf_documents()
    docs = knowledge_docs + pdf_docs
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

    docs = load_all_documents()
    store = FAISS.from_documents(docs, get_embeddings())
    store.save_local(str(VECTORSTORE_DIR))

    meta_path = VECTORSTORE_DIR / "build_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "chunk_count": len(docs),
                "sources": sorted({d.metadata.get("source_file") for d in docs}),
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


def _keyword_retrieve(query: str, docs: list[Document], k: int) -> list[Document]:
    """Simple fallback retriever for MOCK mode / no embeddings."""
    tokens = [t for t in re.split(r"\s+|，|。|、|；", query) if len(t) >= 2]
    scored: list[tuple[int, Document]] = []
    for doc in docs:
        text = doc.page_content
        score = sum(text.count(t) for t in tokens)
        # Strong preference for lesson-aligned knowledge sections
        for phrase in ("示例课时对齐", "有理数的加法", "内容要点（教学对齐用）", "学业要求（对齐表述）"):
            if phrase in text:
                score += 20
        for boost in ("有理数", "数与代数", "教学提示", "核心素养", "第四学段"):
            if boost in query and boost in text:
                score += 2
        # Penalize overly generic front-matter
        if text.lstrip().startswith("# 义务教育数学课程标准"):
            score -= 5
        scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = [d for s, d in scored[:k] if s > 0]
    return chosen or docs[:k]


def retrieve_curriculum_context(query: str, k: int = RETRIEVE_K) -> tuple[str, list[Document]]:
    docs_all = load_all_documents()

    # MOCK，或尚未建好向量库时：关键词检索兜底
    index_file = VECTORSTORE_DIR / "index.faiss"
    if MOCK_LLM or not index_file.exists():
        picked = _keyword_retrieve(query, docs_all, k)
        return _format_docs(picked), picked

    try:
        retriever = get_retriever(k=k)
        docs = retriever.invoke(query)
        return _format_docs(docs), docs
    except Exception:
        # Embedding 额度不足等异常时回退关键词检索，保证对话模型仍可演示
        picked = _keyword_retrieve(query, docs_all, k)
        return _format_docs(picked), picked


def _format_docs(docs: list[Document]) -> str:
    blocks: list[str] = []
    for i, doc in enumerate(docs, start=1):
        page = doc.metadata.get("page")
        source = doc.metadata.get("source_file", "课标")
        header = f"[片段{i} | {source} | page={page}]"
        text = re.sub(r"\s+", " ", doc.page_content).strip()
        blocks.append(f"{header}\n{text}")
    return "\n\n".join(blocks)
