"""CLI: python -m ingest_lidar INPUT -o OUT_DIR [--no-llm] [--corrections c.json] [--exit-tags t.json]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ingest_lidar", description="LiDAR scan -> scene.json")
    ap.add_argument("input", type=Path, help="mesh/point cloud (obj, ply, glb, usdz) or RoomPlan structure.json")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output dir, e.g. out/<run_id>")
    ap.add_argument("--no-llm", action="store_true", help="skip LLM semantics (room names/types)")
    ap.add_argument("--corrections", type=Path, help="corrections.json: {exit_tags, up, scale}")
    ap.add_argument("--exit-tags", type=Path, help="exit_tags.json: [[x, z, label], ...] in scene frame")
    ap.add_argument("--up", help="force the input file's up axis, e.g. +y or -z")
    ap.add_argument("--scale", type=float, help="multiply input coordinates by this factor")
    a = ap.parse_args(argv)
    scene = run(a.input, a.out, no_llm=a.no_llm, corrections=a.corrections, exit_tags=a.exit_tags,
                up=a.up, scale=a.scale)
    errors = [i for i in scene["extensions"]["lidar"]["validation"] if i["severity"] == "error"]
    print(f"wrote {a.out / 'scene.json'}: {len(scene['rooms'])} rooms, {len(scene['doors'])} doors, "
          f"{len(scene['exits'])} exits, validator errors: {len(errors)}")
    for e in errors:
        print(f"  ERROR {e['code']} {e.get('ref') or ''}: {e['message']}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
