from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ensure_venv() -> None:
    """若未在项目 .venv 中，则用 venv 解释器重新执行本脚本。"""
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if os.name != "nt":
        venv_python = ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        print(
            f"未找到虚拟环境: {venv_python}\n"
            "请先执行: python -m venv .venv && .\\.venv\\Scripts\\pip install -r requirements.txt",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)

    in_venv = Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()
    if in_venv:
        return

    print(f"检测到非项目虚拟环境，改用: {venv_python}", flush=True)
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_venv()
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402


if __name__ == "__main__":
    print("Starting API on http://127.0.0.1:8000", flush=True)
    uvicorn.run(
        "lesson_prep.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
