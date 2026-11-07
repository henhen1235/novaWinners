"""Run one sim (scene + scenario + seed + decider) in-process and summarize it for the loop.

Writes replay.json, decisions.jsonl, metrics.json (sim/output.py) plus summary.json:
the scoreboard numbers and the decision-log digest the optimizer reads. Times in s.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path

import numpy as np

from sim.engine import Sim
from sim.output import write_run

CLEAR_SHARE = 0.95
QUEUE_ZONE_M = 5.0
WHY_SAMPLES = 6


def _decider(mode: str, run_dir: Path, run_id: str, no_llm: bool):
    """(decide, decide_batch) for 'policy' or 'cognition'; cognition replays run_dir/decisions.jsonl if present."""
    if mode == "policy":
        from sim.policy import decide
        return decide, None
    import cognition as cog
    log = run_dir / "decisions.jsonl"
    cog.configure(offline=no_llm, replay=log if log.exists() else None, run_id=run_id, reset=True)
    return cog.decide, cog.decide_batch


def _pa_percepts(scenario: dict, decide, batch):
    """Wrap the decider so official PA messages reach cognition as kind 'pa' with their claim.

    ponytail: the sim sets the exit_open belief itself; cognition reads percept kind/claim only,
    so drop this once sim percepts carry kind 'pa' and the claim."""
    by_text = {(e.get("params") or {}).get("text"): (e.get("params") or {}).get("claim") or {}
               for e in scenario.get("events", []) if e["type"] == "pa_announcement"}
    if not by_text:
        return decide, batch

    def fix(view: dict) -> dict:
        ps = []
        for p in view.get("percepts", []):
            if p.get("kind") == "message" and p.get("official") and p.get("text") in by_text:
                p = {**p, "kind": "pa", "source_kind": "pa", "claim": {**by_text[p["text"]], "told_to_leave": True}}
            ps.append(p)
        return {**view, "percepts": ps}

    return (lambda v: decide(fix(v))), (None if batch is None else lambda vs: batch([fix(v) for v in vs]))


def run_one(scene: dict, scenario: dict, seed: int, run_dir: Path, decide_mode: str = "policy",
            no_llm: bool = False, n_original: int | None = None, scene_path: str = "",
            scenario_path: str = "") -> dict:
    """Run, write the run directory, return summary (also written as summary.json)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{run_dir.parent.name}_{run_dir.name}"
    decide, batch = _decider(decide_mode, run_dir, run_id, no_llm)
    decide, batch = _pa_percepts(scenario, decide, batch)
    t0 = time.perf_counter()
    sim = Sim(scene, scenario, seed, decide, batch, run_id=run_id)
    sim.run()
    wall = time.perf_counter() - t0
    body = write_run(sim, run_dir, scene_path, scenario_path)
    summary = summarize(sim, body, n_original, wall)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def _tick_latency(records: list[dict]) -> dict:
    """Per-tick LLM latency (the slowest call of each tick's parallel batch), ms."""
    per_tick: dict[int, int] = {}
    for r in records:
        if r.get("raw_output"):
            per_tick[r["tick"]] = max(per_tick.get(r["tick"], 0), int(r.get("latency_ms") or 0))
    xs = sorted(v for v in per_tick.values() if v > 0)
    if not xs:
        return {"ticks_with_llm": len(per_tick), "p50_ms": None, "p90_ms": None, "max_ms": None}
    return {"ticks_with_llm": len(per_tick), "p50_ms": xs[len(xs) // 2], "p90_ms": xs[int(0.9 * (len(xs) - 1))],
            "max_ms": xs[-1]}


def _queues(sim: Sim, frames: list[dict]) -> tuple[int, dict]:
    """Max agents queued in one frame, and per exit the max agents still inside within QUEUE_ZONE_M of it."""
    goals = {e: ex.goal for e, ex in sim.world.exits.items()}
    max_q, per_exit = 0, {e: 0 for e in goals}
    for f in frames:
        inside = [r for r in f["a"] if r[3] != "exited"]
        max_q = max(max_q, sum(r[3] == "queued" for r in inside))
        if not inside:
            continue
        pts = np.array([[r[1], r[2]] for r in inside])
        for e, g in goals.items():
            per_exit[e] = max(per_exit[e], int(np.sum(np.linalg.norm(pts - g, axis=1) <= QUEUE_ZONE_M)))
    return max_q, per_exit


def _why(rec: dict) -> str:
    padm = ((rec.get("decision") or {}).get("padm") or {})
    return str(padm.get("why") or "")[:160]


def summarize(sim: Sim, body: dict, n_original: int | None, wall_s: float) -> dict:
    """Scoreboard numbers over the original occupants (added wardens are not counted) plus a digest."""
    m = body["metrics"]
    orig = sim.agents[:n_original] if n_original else sim.agents
    ids = {a.id for a in orig}
    n = len(orig)
    times = sorted(a.exit_t - sim.alarm_t for a in orig if a.exit_t is not None)
    need = math.ceil(CLEAR_SHARE * n)
    t95 = round(times[need - 1], 1) if n and len(times) >= need else None
    need90 = math.ceil(0.9 * n)
    t90 = round(times[need90 - 1], 1) if n and len(times) >= need90 else None
    misled = [x for x in m.get("misled_by_rumor", []) if x in ids]
    max_q, exit_q = _queues(sim, body["frames"])
    strag = [a for a in orig if a.exit_t is None]
    recs = sim.records
    misled_recs = [r for r in recs if r["agent_id"] in set(misled)]
    strag_recs = [r for r in recs if r["agent_id"] in {a.id for a in strag}]
    return {
        "t95_s": t95, "t95_reached": t95 is not None, "t90_s": t90,
        "t_last_s": round(times[-1], 1) if times else None, "duration_s": sim.duration,
        "evacuated": len(times), "total": n, "stragglers": len(strag), "misled_by_rumor": len(misled),
        "max_queue": max_q, "max_near_exit": exit_q, "exit_usage": m["exit_usage"],
        "pre_move_median_s": m["pre_movement_s"]["median"], "decisions": len(recs),
        "llm_calls": sim.llm_calls, "cost_usd": round(sim.cost_usd, 6), "wall_s": round(wall_s, 2),
        "latency": _tick_latency(recs),
        "stragglers_by_room": dict(Counter(a.spawn_room for a in strag)),
        "stragglers_by_state": dict(Counter(a.state for a in strag)),
        "stragglers_by_persona": dict(Counter(a.persona for a in strag)),
        "misled_by_room": dict(Counter(a.spawn_room for a in orig if a.id in set(misled))),
        "exit_by_room": {r: dict(Counter(a.exit_id for a in orig if a.spawn_room == r))
                         for r in sorted({a.spawn_room for a in orig if a.spawn_room})},
        "why_samples": [f"{r['agent_id']} t={r['t_s']}: {_why(r)}" for r in (misled_recs + strag_recs)[-WHY_SAMPLES:]],
    }
