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
    wps = nav.find_path(nav.snap([2.5, 2.0]), nav.snap([13.5, 8.0]))  # office N1 to office S3
    assert wps is not None and len(wps) >= 3
    path = [w.pt for w in wps]
    walk = nav.walk[0].buffer(1e-3)
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


SCHOOL = json.loads((Path(__file__).parents[1] / "fixtures/two_floor_school.scene.json").read_text())


def _school(events: list[dict], occupants: list[dict], duration: float = 300, seed: int = 5,
            tweak: dict | None = None) -> Sim:
    sim = Sim(SCHOOL, {"duration_s": duration, "events": events, "occupants": occupants}, seed)
    for a in sim.agents:
        a.traits.update((tweak or {}).get(a.persona, {}))
    sim.run()
    return sim


def test_upper_floor_reaches_ground_exits_by_stairs() -> None:
    occ = [{"where": {"room": "L2_class_n2"}, "count": 8, "personas": {"regular": 1.0}}]
    sim = _school([], occ, tweak={"regular": {"premove_s": 5.0}})
    assert all(a.exit_t is not None for a in sim.agents)
    assert {sid for _, sid, _ in sim.crossings if sid.startswith("stair")}, "nobody used a stair"
    assert all(len(f["a"][0]) == 5 for f in sim.frames)  # [id, x, z, state, level]


def test_blocked_stair_reroutes_to_the_other() -> None:
    occ = [{"where": {"room": "L2_class_n1"}, "count": 6, "personas": {"regular": 1.0}}]  # west side, near stair_01
    ev = [{"id": "b", "type": "blocked_exit", "t_start_s": 0, "at": {"stair": "stair_01"}}]
    sim = _school(ev, occ, tweak={"regular": {"premove_s": 5.0}})
    used = [sid for _, sid, _ in sim.crossings if sid.startswith("stair")]
    assert used and "stair_01" not in used
    assert all(a.exit_t is not None for a in sim.agents)


def test_mobility_impaired_waits_at_refuge_and_is_carried() -> None:
    occ = [{"where": {"room": "L2_class_s1"}, "count": 1, "personas": {"mobility_impaired": 1.0}},
           {"where": {"room": "L1_corridor"}, "count": 1, "personas": {"staff": 1.0}}]
    tweak = {"mobility_impaired": {"premove_s": 0.0}, "staff": {"premove_s": 1e6, "notice_s": 1e6}}
    sim = _school([], occ, duration=400, tweak=tweak)
    kinds = [e["kind"] for e in sim.replay_events]
    assert "waiting_at_refuge" in kinds
    assert "helping" in kinds
    assert all(a.exit_t is not None for a in sim.agents)
