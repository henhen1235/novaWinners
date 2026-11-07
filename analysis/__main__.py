"""python -m analysis SCENE.json -o analysis.json [--svg plan_analysis.svg]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import occupancy, pinch, smoke, spof
from .render import render_with_analysis


def analyze(scene: dict) -> dict:
    occ = occupancy.analyze(scene)
    loads = {k: v["load"] for k, v in occ["rooms"].items()}
    return {"scene": scene["meta"]["name"], "occupancy": occ, "spof": spof.analyze(scene, loads),
            "pinch": pinch.analyze(scene), "smoke": smoke.analyze(scene)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic egress analysis of a scene.json")
    ap.add_argument("scene", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="analysis.json (default: next to scene.json)")
    ap.add_argument("--svg", type=Path, help="overlay SVG (default: plan_analysis.svg next to the output)")
    a = ap.parse_args(argv)
    scene = json.loads(a.scene.read_text())
    out = a.out or a.scene.with_name("analysis.json")
    result = analyze(scene)
    out.write_text(json.dumps(result, indent=1) + "\n")
    svg = a.svg or out.with_name("plan_analysis.svg")
    svg.write_text(render_with_analysis(scene, result))
    sp, pr = result["spof"], result["pinch"]
    print(f"wrote {out} and {svg}")
    print(f"occupant load {result['occupancy']['total']}; articulation rooms {sp['articulation_rooms']}; "
          f"narrowest route {pr['narrowest']}; flagged <0.9 m {pr['flagged']}")
    for row in sp["ranked"][:5]:
        print(f"  SPOF {row['kind']} {row['id']}: strands {len(row['stranded_rooms'])} rooms "
              f"({row['stranded_occupants']} occ), exit width left {row['exit_width_left_share']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
