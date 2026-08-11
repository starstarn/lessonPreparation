from __future__ import annotations

"""单次备课任务入口：由 JobStore 以子进程方式调用，避免 Windows 线程 Errno 22。"""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: run_job.py <input.json> <output.json> [resume_from] [prior.json]",
            file=sys.stderr,
        )
        return 2

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    resume_from = sys.argv[3].strip() if len(sys.argv) > 3 and sys.argv[3].strip() else None
    prior_path = Path(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].strip() else None

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    prior_state = None
    if prior_path and prior_path.exists():
        prior_state = json.loads(prior_path.read_text(encoding="utf-8"))

    progress_path = output_path.with_suffix(".progress.json")
    checkpoint_path = output_path.with_suffix(".checkpoint.json")

    def on_progress(step: str, message: str) -> None:
        progress_path.write_text(
            json.dumps({"step": step, "message": message}, ensure_ascii=False),
            encoding="utf-8",
        )

    def on_checkpoint(partial: dict) -> None:
        checkpoint_path.write_text(
            json.dumps(partial, ensure_ascii=False),
            encoding="utf-8",
        )

    try:
        from lesson_prep.graph import run_preparation

        result = run_preparation(
            payload,
            on_progress=on_progress,
            resume_from=resume_from,  # type: ignore[arg-type]
            prior_state=prior_state,
            on_checkpoint=on_checkpoint,
        )
        output_path.write_text(
            json.dumps({"ok": True, "result": result}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback

        partial = None
        if checkpoint_path.exists():
            try:
                partial = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                partial = None

        output_path.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{exc}\n{traceback.format_exc()}",
                    "partial_result": partial,
                    "failed_step": resume_from,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return 1


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    raise SystemExit(main())
