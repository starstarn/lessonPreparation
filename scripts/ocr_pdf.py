from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fitz
from PIL import Image
from rich.console import Console

from lesson_prep.config import DOC_DIR, find_curriculum_pdf

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="对扫描版课标 PDF 做 OCR，写入 knowledge/")
    parser.add_argument("--start", type=int, default=0, help="起始页（含）")
    parser.add_argument("--end", type=int, default=None, help="结束页（不含），默认到末尾")
    parser.add_argument("--zoom", type=float, default=1.5, help="渲染倍率，越大越清晰越慢")
    parser.add_argument(
        "--out",
        type=Path,
        default=DOC_DIR / "knowledge" / "math_curriculum_ocr.txt",
        help="输出文本路径",
    )
    args = parser.parse_args()

    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise SystemExit("请先安装: pip install rapidocr-onnxruntime pymupdf pillow") from exc

    pdf_path = find_curriculum_pdf()
    doc = fitz.open(pdf_path)
    start = max(0, args.start)
    end = args.end if args.end is not None else doc.page_count
    end = min(end, doc.page_count)
    total = end - start

    args.out.parent.mkdir(parents=True, exist_ok=True)
    ocr = RapidOCR()

    # 边识别边落盘，中断也不丢已完成页
    with args.out.open("w", encoding="utf-8") as f:
        f.write(f"# OCR from {pdf_path.name}\n")
        f.write(f"# pages {start}-{end - 1}\n\n")
        f.flush()

        console.print(f"OCR {pdf_path.name} pages [{start}, {end}) -> {args.out}")
        for n, i in enumerate(range(start, end), start=1):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(args.zoom, args.zoom))
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            result, _elapse = ocr(image)
            lines = [row[1] for row in result] if result else []
            f.write(f"\n\n===== PAGE {i + 1} =====\n\n")
            f.write("\n".join(lines))
            f.write("\n")
            f.flush()
            console.print(f"[{n}/{total}] page {i + 1} ok ({len(lines)} lines)")

    console.print(f"[green]完成[/green]：{args.out}")


if __name__ == "__main__":
    main()
