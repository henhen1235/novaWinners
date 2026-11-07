"""Sim checks on fixtures/tiny_office.scene.json (meters, seconds)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely.geometry import LineString

from sim.engine import Sim
from sim.navmesh import NavMesh
from sim.output import replay
from sim.world import FLOW_P_S_M

SCENE = json.loads((Path(__file__).parents[1] / "fixtures/tiny_office.scene.json").read_text())


def _scenario(events: list[dict], occupants: list[dict] | None = None, duration: float = 240) -> dict:
    occ = occupants or [{"where": {"room": r}, "count": 5, "personas": {"regular": 0.7, "visitor": 0.3}}
                        for r in ("L1_office_n1", "L1_office_n2", "L1_office_s1", "L1_office_s3")]
    return {"schema_version": "0.1", "duration_s": duration, "events": events, "occupants": occ}


def _run(scenario: dict, seed: int = 7, omniscient: bool = False) -> Sim:
    sim = Sim(SCENE, scenario, seed, omniscient=omniscient)
    sim.run()
    return sim


def test_path_goes_through_doors_not_walls() -> None:
    nav = NavMesh(SCENE)
    path = nav.find_path(nav.snap([2.5, 2.0]), nav.snap([13.5, 8.0]))  # office N1 to office S3
    assert path is not None and len(path) >= 3
    walk = nav.walk.buffer(1e-3)
    line = LineString(path)
    assert walk.covers(line)
    for door in ("door_01", "door_06"):
        seg = next(d["segment"] for d in SCENE["doors"] if d["id"] == door)
        assert line.intersects(LineString(seg)), f"path does not pass {door}"


def test_everyone_exits_without_events() -> None:
    sim = _run(_scenario([], duration=900))  # lognormal pre-movement has a long tail
    assert all(a.exit_t is not None for a in sim.agents), [a.id for a in sim.agents if a.exit_t is None]


def test_blocked_exit_never_used_after_block() -> None:
    t_block = 10.0
    sim = _run(_scenario([{"id": "b", "type": "blocked_exit", "t_start_s": t_block, "at": {"exit": "exit_02"}}]))
    late = [e for e in sim.replay_events if e["kind"] == "exited" and e["ref"] == "exit_02" and e["t"] >= t_block]
    assert not late
    assert sum(a.exit_t is not None for a in sim.agents) >= len(sim.agents) - 2


def test_same_seed_same_replay() -> None:
    scen = _scenario([{"id": "f", "type": "fire", "t_start_s": 0, "at": {"room": "L1_office_n2"}},
                      {"id": "r", "type": "rumor", "t_start_s": 20, "at": {"room": "L1_corridor"},
                       "params": {"claim": {"exit_blocked": "exit_02"}}}], duration=120)
    a = json.dumps(replay(_run(scen, seed=3), "s", "c"))
    b = json.dumps(replay(_run(scen, seed=3), "s", "c"))
    assert a == b


def test_door_flow_cap() -> None:
    occ = [{"where": {"room": "L1_office_n1"}, "count": 20, "personas": {"staff": 1.0}}]
    sim = _run(_scenario([], occ, duration=120), omniscient=True)
    times = np.array([t for t, door, _ in sim.crossings if door == "door_01"])
    assert len(times) >= 15
    cap = FLOW_P_S_M * 0.9
    window = 10.0
    for t0 in np.arange(0, times.max(), 0.5):
        n = int(np.sum((times >= t0) & (times < t0 + window)))
        assert n <= cap * window + 1 + cap * 0.5, (t0, n)
