"""Run the pipeline on every ``<dir>/plan.*`` with a ``truth.scene.json`` and print one score row each.

    python -m tools.raster_bench out/bench/* --out out/bench_run

Columns match ``score_against_truth.py`` plus validator errors and runtime.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ingest_blueprint.pipeline import run
from tools.score_against_truth import load_scene, score_scenes

COLUMNS = ("walkable_iou", "room_delta", "door_recall", "door_precision", "exit_recall", "scale_error")


def bench(plan_dirs: list[Path], out_root: Path) -> list[dict]:
    rows = []
    for directory in plan_dirs:
        plan = next((directory / f"plan{ext}" for ext in (".png", ".jpg", ".pdf") if (directory / f"plan{ext}").is_file()), None)
        truth = directory / "truth.scene.json"
        if plan is None or not truth.is_file():
            continue
        start = time.perf_counter()
        scene, code = run(plan, out_root / directory.name, no_llm=True)
        seconds = time.perf_counter() - start
        score = score_scenes(scene, load_scene(truth))
        errors = sum(1 for warning in scene["warnings"] if warning["severity"] == "error")
        rows.append({"name": directory.name, **{key: score[key] for key in COLUMNS}, "errors": errors, "seconds": round(seconds, 1)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dirs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = bench(args.dirs, args.out)
    header = ("name",) + COLUMNS + ("errors", "seconds")
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for row in rows:
        print("| " + " | ".join(str(row[key]) for key in header) + " |")


if __name__ == "__main__":
    main()
