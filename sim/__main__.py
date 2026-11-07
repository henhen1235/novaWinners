"""CLI.

    python -m sim run --scene out/hall/scene.json --scenario s.json -o out/sim/hall_fire --seed 42 \
        [--decide cognition | --decide pkg.mod:func] [--no-llm]
    python -m sim baseline --scene ... --scenario ... -o out/sim/hall_base --seed 42
    python -m sim compare out/sim/hall_fire out/sim/hall_base
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import time
from pathlib import Path

from sim.engine import Sim
from sim.output import write_run
from sim.policy import baseline_decide, decide as default_decide

log = logging.getLogger("sim")


def load_decider(spec: str | None) -> tuple[object, object]:
    """(decide, decide_batch or None) from 'module' or 'module:function'; default policy on failure."""
    if not spec or spec == "default":
        return default_decide, None
    mod_name, _, fn = spec.partition(":")
    try:
        mod = importlib.import_module(mod_name)
        decide = getattr(mod, fn or "decide")
    except (ImportError, AttributeError) as exc:
        log.warning("could not load decider %r (%s); using the default rule-based policy", spec, exc)
        return default_decide, None
    return decide, getattr(mod, "decide_batch", None) if not fn else None


def _resolve_scene(args: argparse.Namespace, scenario: dict) -> Path:
    raw = args.scene or scenario.get("scene")
    if not raw:
        sys.exit("no scene: pass --scene or set scenario.scene")
    p = Path(raw)
    if not p.exists() and not p.is_absolute():
        p = Path(args.scenario).parent / raw
    if not p.exists():
        sys.exit(f"scene not found: {raw}")
    return p


COMPARE_ROWS = [
    ("evacuated", lambda m: f"{m['evacuated']}/{m['total']}"),
    ("last exit s", lambda m: m["t_last_exit_s"]),
    ("median exit s", lambda m: m["exit_time_s"]["median"]),
    ("pre-move median s", lambda m: m["pre_movement_s"]["median"]),
    ("stragglers", lambda m: len(m["stragglers"])),
    ("misled by rumor", lambda m: len(m.get("misled_by_rumor", []))),
    ("exit usage", lambda m: " ".join(f"{k}:{v}" for k, v in m["exit_usage"].items())),
]


def compare(run_dirs: list[str]) -> int:
    """Print the metrics of several run directories side by side."""
    ms = [json.loads((Path(d) / "metrics.json").read_text()) for d in run_dirs]
    names = [Path(d).name for d in run_dirs]
    width = max(len(n) for n in names + ["x" * 12]) + 2
    print(f"{'':20}" + "".join(f"{n:>{width}}" for n in names))
    for label, fn in COMPARE_ROWS:
        print(f"{label:20}" + "".join(f"{str(fn(m)):>{width}}" for m in ms))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["compare"]:
        return compare(argv[1:])
    ap = argparse.ArgumentParser(prog="python -m sim")
    ap.add_argument("mode", choices=["run", "baseline"])
    ap.add_argument("--scene")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--decide", help="'cognition' or 'module:function' (default: built-in rules)")
    ap.add_argument("--no-llm", action="store_true", help="sets NOVA_NO_LLM=1 for the decider")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    scenario = json.loads(Path(args.scenario).read_text())
    scene_path = _resolve_scene(args, scenario)
    scene = json.loads(scene_path.read_text())
    seed = args.seed if args.seed is not None else int(scenario.get("seed", 0))
    out = Path(args.out)
    if args.no_llm:
        os.environ["NOVA_NO_LLM"] = "1"
    os.environ["NOVA_RUN_DIR"] = str(out)
    if args.mode == "baseline":
        decide, batch, omni = baseline_decide, None, True
    else:
        (decide, batch), omni = load_decider(args.decide), False
    t0 = time.perf_counter()
    sim = Sim(scene, scenario, seed, decide, batch, omniscient=omni, run_id=f"{out.name}_s{seed}")
    sim.run()
    body = write_run(sim, out, str(scene_path), args.scenario)
    m = body["metrics"]
    print(f"{args.mode}: {m['evacuated']}/{m['total']} out, last exit {m['t_last_exit_s']} s, "
          f"exits {m['exit_usage']}, pre-move median {m['pre_movement_s']['median']} s, "
          f"{m['decisions']} decisions, {len(sim.frames)} frames, {time.perf_counter() - t0:.2f} s wall -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
