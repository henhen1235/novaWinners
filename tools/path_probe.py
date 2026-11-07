"""Headless path probe. Prints meters and time at 1.3 m/s to the nearest exit.

    python tools/path_probe.py scene.json
    python tools/path_probe.py scene.json --room r_L1_001
    python tools/path_probe.py scene.json --x 1.2 --z 3.4
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nav.pathfind import path_to_exit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Distance from a point to the nearest exit")
    parser.add_argument("scene")
    parser.add_argument("--room", default=None)
    parser.add_argument("--x", type=float, default=None)
    parser.add_argument("--z", type=float, default=None)
    args = parser.parse_args(argv)
    scene = json.loads(Path(args.scene).read_text(encoding="utf-8"))
    if args.x is not None and args.z is not None:
        start = (args.x, args.z)
        level = scene["levels"][0]["id"]
        label = f"{args.x:.2f},{args.z:.2f}"
    else:
        rooms = scene["rooms"]
        room = next((item for item in rooms if item["id"] == args.room), None) if args.room else None
        if room is None:
            room = rooms[0]
        start = (float(room["centroid"][0]), float(room["centroid"][1]))
        level = room["level"]
        label = room["id"]
    result = path_to_exit(scene["navmesh"], start, scene["exits"], level)
    distance = result["distance_m"]
    if distance is None:
        print(f"{label}: no path")
        return 1
    print(f"{label}: {distance:.2f} m, {result['time_s']:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
