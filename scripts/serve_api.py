from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn


if __name__ == "__main__":
    print("Starting API on http://127.0.0.1:8000", flush=True)
    uvicorn.run(
        "lesson_prep.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
