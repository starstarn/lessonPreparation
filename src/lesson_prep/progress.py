from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

# (step, message, detail)
ProgressCb = Callable[[str, str, dict[str, Any] | None], None]

_local = threading.local()
_lanes_lock = threading.Lock()
_materials_lanes: dict[str, dict[str, str]] = {}

LANE_LABELS = {
    "exercises": "习题组卷师",
    "slides": "课件生成师",
    "blackboard": "板书设计师",
}


def set_progress_callback(cb: ProgressCb | None) -> None:
    _local.callback = cb


def report_progress(step: str, message: str, detail: dict[str, Any] | None = None) -> None:
    cb = getattr(_local, "callback", None)
    if not cb:
        return
    payload = detail
    with _lanes_lock:
        if _materials_lanes and (detail is None or "lanes" not in detail):
            payload = {**(detail or {}), "lanes": dict(_materials_lanes)}
        elif detail and "lanes" in detail:
            payload = detail
    try:
        cb(step, message, payload)
    except TypeError:
        # 兼容旧回调签名 (step, message)
        cb(step, message)  # type: ignore[call-arg]


def reset_materials_lanes(
    targets: list[str] | set[str],
    *,
    clear_others: bool = True,
) -> dict[str, dict[str, str]]:
    """将指定分路重置为 pending。

    clear_others=True：清空其它路（整轮并行启动）。
    clear_others=False：保留其它路状态（单路重跑 / 一致性打回）。
    """
    with _lanes_lock:
        if clear_others:
            _materials_lanes.clear()
        for name in targets:
            _materials_lanes[name] = {
                "status": "pending",
                "label": LANE_LABELS.get(name, name),
                "error": "",
            }
        return dict(_materials_lanes)


def seed_materials_lanes(lanes: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """用已有分路状态覆盖全局（用于单路重跑保留其它路）。"""
    with _lanes_lock:
        _materials_lanes.clear()
        if lanes:
            for k, v in lanes.items():
                if not isinstance(v, dict):
                    continue
                _materials_lanes[k] = {
                    "status": str(v.get("status") or "done"),
                    "label": str(v.get("label") or LANE_LABELS.get(k, k)),
                    "error": str(v.get("error") or "")[:300],
                }
        return dict(_materials_lanes)


def set_lane_status(
    name: str,
    status: str,
    *,
    error: str = "",
    report: bool = True,
) -> dict[str, dict[str, str]]:
    """更新某一路状态：pending / running / done / error。"""
    with _lanes_lock:
        prev = _materials_lanes.get(name) or {
            "label": LANE_LABELS.get(name, name),
            "status": "pending",
            "error": "",
        }
        _materials_lanes[name] = {
            "label": prev.get("label") or LANE_LABELS.get(name, name),
            "status": status,
            "error": (error or "")[:300],
        }
        lanes = dict(_materials_lanes)

    if report:
        summary = _lanes_summary(lanes)
        report_progress("materials", summary, {"lanes": lanes})
    return lanes


def get_materials_lanes() -> dict[str, dict[str, str]]:
    with _lanes_lock:
        return dict(_materials_lanes)


def clear_materials_lanes() -> None:
    with _lanes_lock:
        _materials_lanes.clear()


def _lanes_summary(lanes: dict[str, dict[str, str]]) -> str:
    parts: list[str] = []
    for key in ("slides", "exercises", "blackboard"):
        lane = lanes.get(key)
        if not lane:
            continue
        label = lane.get("label") or key
        st = lane.get("status") or "pending"
        if st == "done":
            parts.append(f"{label}+")
        elif st == "error":
            parts.append(f"{label}x")
        elif st == "running":
            parts.append(f"{label}...")
        else:
            parts.append(f"{label}.")
    done = sum(1 for v in lanes.values() if v.get("status") == "done")
    err = sum(1 for v in lanes.values() if v.get("status") == "error")
    total = len(lanes) or 1
    head = f"并行生成 {done}/{total}"
    if err:
        head += f"（失败 {err}）"
    return f"{head}：{' '.join(parts)}" if parts else head
