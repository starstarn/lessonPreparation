from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from lesson_prep.config import OUTPUT_DIR

VERSIONS_DIR = OUTPUT_DIR / "versions"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_dir() -> None:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _path(version_id: str) -> Path:
    return VERSIONS_DIR / f"{version_id}.json"


def save_version(
    *,
    lesson_input: dict[str, Any],
    result: dict[str, Any],
    note: str = "",
    title: str | None = None,
    version_id: str | None = None,
) -> dict[str, Any]:
    _ensure_dir()
    vid = version_id or uuid.uuid4().hex[:12]
    path = _path(vid)
    created_at = _now()
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        created_at = old.get("created_at") or created_at

    grade = lesson_input.get("grade") or ""
    lesson_title = lesson_input.get("lesson_title") or "未命名课时"
    payload = {
        "id": vid,
        "title": title or f"{grade}-{lesson_title}".strip("-"),
        "note": note,
        "input": lesson_input,
        "result": result,
        "created_at": created_at,
        "updated_at": _now(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def list_versions() -> list[dict[str, Any]]:
    _ensure_dir()
    items: list[dict[str, Any]] = []
    for path in sorted(VERSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append(
                {
                    "id": data.get("id") or path.stem,
                    "title": data.get("title") or path.stem,
                    "note": data.get("note") or "",
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "lesson_title": (data.get("input") or {}).get("lesson_title"),
                    "grade": (data.get("input") or {}).get("grade"),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return items


def get_version(version_id: str) -> dict[str, Any] | None:
    path = _path(version_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def delete_version(version_id: str) -> bool:
    path = _path(version_id)
    if not path.exists():
        return False
    path.unlink()
    return True
