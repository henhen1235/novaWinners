"""python -m scenario {make,chat,seed,validate} SCENE.json ...

  make SCENE "fire in the corridor, 30 people" -o scenario.json   one shot, no questions
  chat SCENE -o scenario.json                                      interactive REPL
  seed SCENE -o scenario.json [--no-llm]                           fill the occupants block
  validate SCENE scenario.json
  populate SCENE --total 120 [--mode load|random|llm] [--preset office_day|dorm_night|event|custom]
           [--personas JSON] [--levels L1,L2] [--seed 42] [-o scenario.json | --into existing.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .chat import one_shot_answer, run
from .schema import validate
from .populate import MODES, PRESETS, populate
from .seed import seed


def _ask_tty(question: str, options: list[str]) -> str:
    print(f"\n? {question}")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    ans = input("> ").strip()
    return options[int(ans) - 1] if ans.isdigit() and 0 < int(ans) <= len(options) else ans


def _write(path: Path, scenario: dict) -> None:
    path.write_text(json.dumps(scenario, indent=1) + "\n")
    print(f"wrote {path}")


def _chat(scene: dict, a, request: str, ask, tool_choice: str = "auto") -> int:
    scenario, cost, _ = run(scene, str(a.scene), request, ask, log=lambda s: print(f"  [tool] {s}", file=sys.stderr),
                            tool_choice=tool_choice)
    print(f"LLM cost ${cost:.5f}", file=sys.stderr)
    if scenario is None:
        print("no valid scenario after the turn limit", file=sys.stderr)
        return 1
    _write(a.out, scenario)
    return 0


def _blank(scene_path: Path) -> dict:
    return {"schema_version": "0.1", "scene": str(scene_path), "seed": 42, "duration_s": 300, "events": [],
            "assumptions": []}


def _populate(scene: dict, a) -> int:
    try:
        occupants, notes = populate(scene, a.total, a.mode, a.preset, a.personas, a.levels, a.seed,
                                    use_llm=not a.no_llm)
    except ValueError as e:
        print(f"populate: {e}", file=sys.stderr)
        return 1
    base = json.loads(a.into.read_text()) if a.into else _blank(a.scene)
    scenario = {**base, "occupants": base.get("occupants", []) + occupants,
                "assumptions": base.get("assumptions", []) + notes}
    errors = validate(scenario, scene)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    out = a.out if a.out != Path("scenario.json") or not a.into else a.into
    _write(out, scenario)
    print("\n".join(notes[:2]))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scenario", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("make", "chat", "seed", "validate", "populate"):
        p = sub.add_parser(name)
        p.add_argument("scene", type=Path)
        if name == "make":
            p.add_argument("request")
        if name == "validate":
            p.add_argument("scenario", type=Path)
        else:
            p.add_argument("-o", "--out", type=Path, default=Path("scenario.json"))
        if name in ("seed", "populate"):
            p.add_argument("--no-llm", action="store_true")
        if name == "populate":
            p.add_argument("--total", type=int, required=True)
            p.add_argument("--mode", choices=MODES, default="load")
            p.add_argument("--preset", choices=[*PRESETS, "custom"], default="office_day")
            p.add_argument("--personas", type=json.loads, help='custom mix, e.g. \'{"regular_employee": 1.0}\'')
            p.add_argument("--levels", type=lambda v: [x for x in v.split(",") if x])
            p.add_argument("--seed", type=int, default=42)
            p.add_argument("--into", type=Path, help="append the people to this scenario (written to -o, or in place)")
    a = ap.parse_args(argv)
    scene = json.loads(a.scene.read_text())
    if a.cmd == "make":
        return _chat(scene, a, a.request, one_shot_answer, tool_choice="required")
    if a.cmd == "chat":
        return _chat(scene, a, input("Describe the drill: ").strip(), _ask_tty)
    if a.cmd == "validate":
        errors = validate(json.loads(a.scenario.read_text()), scene)
        print("\n".join(errors) or "ok")
        return 1 if errors else 0
    if a.cmd == "populate":
        return _populate(scene, a)
    occupants, notes = seed(scene, use_llm=not a.no_llm)
    base = json.loads(a.out.read_text()) if a.out.exists() else _blank(a.scene)
    scenario = {**base, "occupants": occupants, "assumptions": base.get("assumptions", []) + notes}
    errors = validate(scenario, scene)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    _write(a.out, scenario)
    print(f"{sum(o['count'] for o in occupants)} occupants in {len(occupants)} rooms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
