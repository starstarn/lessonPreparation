from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from lesson_prep.config import ROOT_DIR

CATALOG_PATH = ROOT_DIR / "doc" / "catalog" / "math_junior.json"


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.exists():
        return {
            "subject": "数学",
            "curriculum_year": "2022",
            "textbook_versions": ["人教版"],
            "stages": [],
        }
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
