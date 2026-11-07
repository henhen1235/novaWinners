"""Metrics and run-directory writers: replay.json, decisions.jsonl, metrics.json.

Times in s.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from sim.agents import PERSONA_COLORS

if TYPE_CHECKING:
    from sim.engine import Sim


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "median": None, "p90": None, "mean": None, "max": None}
    a = np.asarray(xs, dtype=float)
    return {"n": len(xs), "median": round(float(np.median(a)), 1), "p90": round(float(np.percentile(a, 90)), 1),
            "mean": round(float(a.mean()), 1), "max": round(float(a.max()), 1)}


def metrics(sim: "Sim") -> dict:
    """Run summary: evacuation counts, exit usage, pre-movement and per-persona times."""
    out_ = [a for a in sim.agents if a.exit_t is not None]
    pre = [a.first_move_t - sim.alarm_t for a in sim.agents if a.first_move_t is not None]
    per_persona = {}
    for name in sorted({a.persona for a in sim.agents}):
        group = [a for a in sim.agents if a.persona == name]
        per_persona[name] = {
            "total": len(group), "evacuated": sum(a.exit_t is not None for a in group),
            "pre_movement_s": _stats([a.first_move_t - sim.alarm_t for a in group if a.first_move_t is not None]),
            "exit_time_s": _stats([a.exit_t - sim.alarm_t for a in group if a.exit_t is not None]),
        }
    misled = [a.id for a in sim.agents if any(
        b["key"].startswith("exit_open:") and b["value"] is False and b["source"]["kind"] != "observation"
        and not sim.world.exits[b["key"].split(":", 1)[1]].blocked
        for b in a.beliefs.values() if b["key"].split(":", 1)[1] in sim.world.exits)]
    usage = Counter(a.exit_id for a in out_)
    return {
        "evacuated": len(out_), "total": len(sim.agents),
        "t_last_exit_s": round(max((a.exit_t for a in out_), default=0.0), 1),
        "exit_usage": {e: usage.get(e, 0) for e in sim.world.exits},
        "pre_movement_s": _stats(pre),
        "exit_time_s": _stats([a.exit_t - sim.alarm_t for a in out_]),
        "per_persona": per_persona,
        "stragglers": [{"agent": a.id, "persona": a.persona, "state": a.state, "target_exit": a.target_exit}
                       for a in sim.agents if a.exit_t is None],
        "misled_by_rumor": misled,
        "decisions": len(sim.records),
        "alarm_t_s": sim.alarm_t,
        "t_end_s": sim.t,
    }


def replay(sim: "Sim", scene_path: str, scenario_path: str) -> dict:
    """replay.json body exactly as docs/SIM_INTERFACES.md, plus a few extra meta keys."""
    return {
        "meta": {"scene": scene_path, "scenario": scenario_path, "seed": sim.seed, "dt": 0.5,
                 "duration_s": sim.duration, "t_end_s": sim.t, "llm_calls": sim.llm_calls,
                 "cost_usd": round(sim.cost_usd, 6), "run_id": sim.run_id,
                 "mode": "baseline" if sim.omniscient else "behavioral",
                 "exits": {e: {"goal": [round(float(v), 2) for v in ex.goal], "blocked": ex.blocked}
                           for e, ex in sim.world.exits.items()}},
        "agents": [{"id": a.id, "persona": a.persona, "model": a.model, "color": PERSONA_COLORS.get(a.persona, "#888")}
                   for a in sim.agents],
        "frames": sim.frames,
        "events": sim.replay_events,
        "metrics": metrics(sim),
    }


def write_run(sim: "Sim", out_dir: Path, scene_path: str, scenario_path: str) -> dict:
    """Write replay.json, decisions.jsonl and metrics.json; return the replay dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    body = replay(sim, scene_path, scenario_path)
    (out_dir / "replay.json").write_text(json.dumps(body, separators=(",", ":")))
    with (out_dir / "decisions.jsonl").open("w") as fh:
        for rec in sim.records:
            fh.write(json.dumps(rec, separators=(",", ":"), default=_json_default) + "\n")
    (out_dir / "metrics.json").write_text(json.dumps(body["metrics"], indent=2))
    return body


def _json_default(o: object) -> object:
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o)}")
