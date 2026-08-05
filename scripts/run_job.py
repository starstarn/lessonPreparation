from __future__ import annotations

"""单次备课任务入口：由 JobStore 以子进程方式调用，避免 Windows 线程 Errno 22。"""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: run_job.py <input.json> <output.json>", file=sys.stderr)
        return 2

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    payload = json.loads(input_path.read_text(encoding="utf-8"))

    # 进度文件：父进程轮询
    progress_path = output_path.with_suffix(".progress.json")

    def on_progress(step: str, message: str) -> None:
        progress_path.write_text(
            json.dumps({"step": step, "message": message}, ensure_ascii=False),
            encoding="utf-8",
        )

    try:
        from lesson_prep.graph import run_preparation

        result = run_preparation(payload, on_progress=on_progress)
        output_path.write_text(
            json.dumps({"ok": True, "result": result}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback

        output_path.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{exc}\n{traceback.format_exc()}",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return 1


if __name__ == "__main__":
    # scripts/run_job.py -> 项目根目录是 parents[1]
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    raise SystemExit(main())
