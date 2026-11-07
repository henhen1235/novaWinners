"""CLI: python -m ingest_plans <floor PDFs or folder> -o OUT [--config NAME|PATH] [--floors 1,2,3|auto]
[--floor-height M] [--no-llm]

Writes OUT/scene.json, OUT/display.glb, OUT/plan.svg and OUT/report.md; exit 1 if the contract
validator reports errors.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import fields
from pathlib import Path

from contract.validate_scene import validate
from ingest_plans.esim import Config, build, order_floors, write_glb
from tools.render_plan import render

CONFIGS = Path(__file__).with_name("configs")
# <BLDG>-<floor>-ESIM-<Base|Type|TypeChart>.pdf; floor ids are letters or digits (A, 1, 12).
SHEET = re.compile(r"-(?P<floor>[A-Za-z0-9]+)-(?:ESIM-)?(?P<kind>Base|TypeChart|Type)\.pdf$", re.I)


def load_config(name_or_path: str | None) -> Config:
    """Config from configs/<name>.json or a path; "extends" chains to a parent config."""
    if not name_or_path:
        return Config()
    path = Path(name_or_path)
    if not path.exists():
        path = CONFIGS / f"{name_or_path}.json"
    data = json.loads(path.read_text())
    merged: dict = {}
    if "extends" in data:
        parent = load_config(data.pop("extends"))
        merged = {f.name: getattr(parent, f.name) for f in fields(Config)}
    data.pop("_comment", None)
    unknown = set(data) - {f.name for f in fields(Config)}
    if unknown:
        raise ValueError(f"{path}: unknown config keys {sorted(unknown)}")
    return Config(**{**merged, **data})


def find_sheets(inputs: list[Path]) -> tuple[dict[str, dict], list[Path]]:
    """floor id -> {"base", "type"} and the color-key PDFs, from file names."""
    pdfs = [p for i in inputs for p in (sorted(i.glob("*.pdf")) if i.is_dir() else [i])]
    sheets: dict[str, dict] = {}
    charts: dict[str, Path] = {}
    for p in pdfs:
        m = SHEET.search(p.name)
        if not m:
            raise ValueError(f"{p.name}: expected <BLDG>-<floor>-ESIM-<Base|Type|TypeChart>.pdf")
        kind, floor = m["kind"].lower(), m["floor"].upper()
        if kind == "typechart":
            charts[floor] = p
        else:
            sheets.setdefault(floor, {"base": None, "type": None, "chart": None})[kind] = p
    for floor, p in charts.items():
        if floor in sheets:
            sheets[floor]["chart"] = p
    missing = [f for f, s in sheets.items() if s["base"] is None]
    if missing:
        raise ValueError(f"no Base sheet for floor(s) {missing}")
    # Without TypeChart sheets the Type sheets print the color key themselves.
    return sheets, list(charts.values()) or [s["type"] for s in sheets.values() if s["type"]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ingest_plans", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="floor plan PDFs or folders of them")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--config", help=f"config name in {CONFIGS} or a JSON path")
    ap.add_argument("--floors", help="comma list bottom to top (e.g. A,1,2,3), or auto")
    ap.add_argument("--floor-height", type=float, help="floor to floor height in meters")
    ap.add_argument("--no-llm", action="store_true", help="accepted for parity; this reader never calls an LLM")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    if a.floor_height:
        cfg.floor_height_m = a.floor_height
    sheets, charts = find_sheets(a.inputs)
    wanted = a.floors.split(",") if a.floors and a.floors != "auto" else cfg.floors
    order = list(wanted) if wanted != "auto" else order_floors(list(sheets), cfg.floor_order)
    absent = [f for f in order if f not in sheets]
    if absent:
        print(f"error: floors {absent} have no sheets (found {sorted(sheets)})", file=sys.stderr)
        return 2
    scene, stats = build({f: sheets[f] for f in order}, charts, cfg)

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "scene.json").write_text(json.dumps(scene, indent=1) + "\n")
    write_glb(scene, a.out / "display.glb")
    (a.out / "plan.svg").write_text(render(scene))
    issues = validate(scene)
    errors = [i for i in issues if i["severity"] == "error"]
    by_code: dict[str, int] = {}
    for i in issues:
        by_code[f"{i['severity']} {i['code']}"] = by_code.get(f"{i['severity']} {i['code']}", 0) + 1
    (a.out / "report.md").write_text(_report(scene, stats, order, by_code))
    print(json.dumps({k: v for k, v in stats.items() if k != "unreachable_ids"}, indent=1))
    print(f"validator: {len(errors)} errors, {len(issues) - len(errors)} warnings -> {a.out}")
    return 1 if errors else 0


def _report(scene: dict, stats: dict, order: list[str], by_code: dict) -> str:
    lines = [f"# {scene['meta']['name']}", "", f"Floors bottom to top: {', '.join(order)}", "",
             "| Floor | m per PDF pt | IQR | rooms used | shift m | door symbols | exterior doors kept |",
             "|---|---|---|---|---|---|---|"]
    for f in order:
        s = stats["floors"][f]
        lines.append(f"| {f} | {s['m_per_pt']} | {s['scale_iqr'][0]}-{s['scale_iqr'][1]} | {s['scale_rooms']} | "
                     f"{s['shift_m']} | {s['door_symbols']} | {s['exits']} |")
    lines += ["", f"Rooms: {stats['rooms']}. Doors: {stats['doors']}. Exits: {stats['exits']}. "
              f"Stairs: {len(scene['stairs'])}. Left out (no path out): {stats['unreachable']}.", "",
              "Validator: " + (", ".join(f"{v} {k}" for k, v in sorted(by_code.items())) or "no issues"), ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
