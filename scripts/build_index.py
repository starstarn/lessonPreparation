from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console

from lesson_prep.config import MOCK_LLM, find_curriculum_pdf
from lesson_prep.rag import build_vectorstore, load_all_documents, load_pdf_documents

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="构建课标向量索引")
    parser.add_argument("--force", action="store_true", help="强制重建索引")
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="仅解析并统计文本块，不调用 Embedding",
    )
    args = parser.parse_args()

    pdf = find_curriculum_pdf()
    console.print(f"[bold]课标 PDF:[/bold] {pdf}")
    pdf_docs = load_pdf_documents(pdf)
    console.print(f"PDF 可抽字块数: {len(pdf_docs)} （扫描件通常为 0）")

    if args.preview_only or MOCK_LLM:
        docs = load_all_documents()
        sources = sorted({d.metadata.get("source_file") for d in docs})
        console.print(f"[green]可用文本块[/green]：{len(docs)}")
        console.print(f"来源: {sources}")
        if docs:
            sample = docs[0]
            path = sample.metadata.get("label_path", "")
            preview = sample.metadata.get("body", sample.page_content)[:120].replace("\n", " ")
            console.print(f"[dim]样例路径:[/dim] {path}")
            console.print(f"[dim]样例正文:[/dim] {preview}...")
            paths = sorted(
                {
                    d.metadata.get("label_path")
                    for d in docs
                    if d.metadata.get("source_kind") == "markdown" and d.metadata.get("label_path")
                }
            )
            console.print(f"层级路径 {len(paths)} 条，例如：{paths[:8]}")
        if MOCK_LLM and not args.preview_only:
            console.print("[yellow]MOCK_LLM=true，跳过真实向量库构建[/yellow]")
        return

    console.print("正在切块并写入 FAISS...")
    store = build_vectorstore(force=args.force)
    console.print(f"[green]完成[/green]：向量数约 {store.index.ntotal}")


if __name__ == "__main__":
    main()
