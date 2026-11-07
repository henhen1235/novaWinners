"""Sim checks on fixtures/tiny_office.scene.json (meters, seconds)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
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


def test_everyone_who_starts_moving_gets_out() -> None:
    sim = _run(_scenario([], duration=900))  # movement layer: nobody gets stuck once walking
    moved = [a for a in sim.agents if a.first_move_t is not None]
    assert moved and all(a.exit_t is not None for a in moved), [a.id for a in moved if a.exit_t is None]


def test_pre_movement_follows_research_lognormals() -> None:
    from sim.personas import PERSONAS, sample_traits
    rng = np.random.default_rng(0)
    for name in ("regular", "visitor", "skeptic"):
        p = PERSONAS[name]
        draws = np.array([sample_traits(p, rng)["premove_s"] for _ in range(4000)])
        p90 = p.premove_median * np.exp(1.2816 * p.premove_sigma)
        assert abs(np.median(draws) / p.premove_median - 1) < 0.08, name
        assert abs(np.percentile(draws, 90) / p90 - 1) < 0.12, name
    # A run with only an alarm: office-style median in minutes-ish range, right-skewed tail (SR143, NCSTAR 1-7).
    sim = _run(_scenario([{"id": "a", "type": "alarm_only", "t_start_s": 0, "at": {"room": "L1_corridor"}}],
                         duration=900))
    pre = np.array([a.first_move_t for a in sim.agents if a.first_move_t is not None])
    assert 20 <= np.median(pre) <= 180 and np.percentile(pre, 90) > 1.5 * np.median(pre)


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
            tweak: dict | None = None, plan: bool = False) -> Sim:
    scen = {"duration_s": duration, "events": events, "occupants": occupants,
            "interventions": [{"type": "assisted_evacuation_plan"}] if plan else []}
    sim = Sim(SCHOOL, scen, seed)
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


def test_without_a_plan_wheelchair_user_waits_at_refuge() -> None:
    occ = [{"where": {"room": "L2_class_s1"}, "count": 1, "personas": {"mobility_impaired": 1.0}},
           {"where": {"room": "L1_corridor"}, "count": 2, "personas": {"staff": 1.0}}]
    sim = _school([], occ, duration=400, tweak={"mobility_impaired": {"premove_s": 0.0}})
    kinds = [e["kind"] for e in sim.replay_events]
    p = next(a for a in sim.agents if a.persona == "mobility_impaired")
    assert "waiting_at_refuge" in kinds and "help_assigned" not in kinds
    assert p.exit_t is None and p.state == "waiting_at_refuge"  # a straggler is a finding, not a bug


def _view(persona: str, waited: float, premove: float, percepts: list[dict]) -> dict:
    from sim.personas import resolve, sample_traits
    tr = {**sample_traits(resolve(persona), np.random.default_rng(0)), "premove_s": premove,
          "t_since_cue_s": waited, "activity": "CONTINUE_TASK", "state": "waiting", "pace": "brisk"}
    return {"agent": {"id": "a_1", "persona": persona, "traits": tr, "entry_exit": "e1", "known_exits": ["e1"]},
            "percepts": [{"id": f"p{i}", **p} for i, p in enumerate(percepts)], "beliefs": [],
            "options": [{"id": "o1", "action": "EVACUATE", "exit": "e1", "route_m": 10, "queue_s": 0, "eta_s": 8},
                        {"id": "o2", "action": "SEEK_INFO"}, {"id": "o3", "action": "CONTINUE_TASK"}]}


def test_skeptic_needs_a_second_cue_and_tails_are_kept() -> None:
    from sim.policy import decide
    alarm = [{"kind": "alarm"}]
    assert decide(_view("skeptic", 60, 800, alarm))["choice"] != "o1"
    assert decide(_view("skeptic", 60, 800, alarm + [{"kind": "people_moving", "count": 2}]))["choice"] == "o1"
    pa = [{"kind": "message", "urgent": True, "official": True, "text": "Evacuate now"}]
    assert decide(_view("skeptic", 60, 800, alarm + pa))["choice"] == "o1"
    assert decide(_view("skeptic", 300, 800, alarm))["choice"] != "o1"  # no cap on the lognormal tail
    assert decide(_view("older_adult", 300, 760, alarm))["choice"] != "o1"
    assert decide(_view("older_adult", 761, 760, alarm))["choice"] == "o1"


def test_assisted_evacuation_plan_nearest_staff_carries_wheelchair_user() -> None:
    occ = [{"where": {"room": "L2_class_s1"}, "count": 1, "personas": {"mobility_impaired": 1.0}},
           {"where": {"room": "L2_class_n3"}, "count": 1, "personas": {"staff": 1.0}},
           {"where": {"room": "L1_class_n1"}, "count": 2, "personas": {"staff": 1.0}}]
    sim = _school([], occ, duration=400, plan=True)
    assigned = [e for e in sim.replay_events if e["kind"] == "help_assigned"]
    staff_l2 = next(a.id for a in sim.agents if a.persona == "staff" and a.spawn_room == "L2_class_n3")
    assert assigned and assigned[0]["agent"] == staff_l2
    assert all(a.exit_t is not None for a in sim.agents)
    assert max(a.helped_count for a in sim.agents) <= 1


def test_official_open_claim_clears_rumor_and_rumor_is_not_an_alarm() -> None:
    ev = [{"id": "r", "type": "rumor", "t_start_s": 5, "at": {"room": "L1_corridor"},
           "params": {"claim": {"exit_blocked": "exit_02"}, "radius_m": 30}},
          {"id": "a", "type": "alarm_only", "t_start_s": 40, "at": {"room": "L1_corridor"}}]
    occ = [{"where": {"room": "L1_corridor"}, "count": 6, "personas": {"regular": 1.0}}]
    sim = Sim(SCENE, _scenario(ev, occ, duration=30), 2)
    sim.run()
    assert all(a.cue_t is None for a in sim.agents)  # the rumor alone started nobody
    heard = [a.beliefs["exit_open:exit_02"] for a in sim.agents]
    assert all(b["value"] is False for b in heard if b["source"]["kind"] != "observation")  # own sight wins
    assert any(b["value"] is False for b in heard)
    ev.append({"id": "pa", "type": "pa_announcement", "t_start_s": 20, "at": {"room": "L1_corridor"},
               "params": {"text": "Main entrance is open", "claim": {"exit_open": "exit_02"}}})
    sim = Sim(SCENE, _scenario(ev, occ, duration=30), 2)
    sim.run()
    assert all(a.beliefs["exit_open:exit_02"]["value"] is True for a in sim.agents)


def test_room_signage_only_reaches_that_room() -> None:
    occ = [{"where": {"room": "L1_office_n1"}, "count": 3, "personas": {"visitor": 1.0}, "familiar_exit": "exit_02"},
           {"where": {"room": "L1_office_s3"}, "count": 3, "personas": {"visitor": 1.0}, "familiar_exit": "exit_02"}]
    scen = {**_scenario([], occ, duration=1), "interventions": [{"type": "signage", "room": "L1_office_n1",
                                                                   "exit": "exit_01", "p": 1.0}]}
    sim = Sim(SCENE, scen, 4)
    n1 = [a for a in sim.agents if a.spawn_room == "L1_office_n1"]
    s3 = [a for a in sim.agents if a.spawn_room == "L1_office_s3"]
    assert all("exit_01" in a.known_exits for a in n1)
    assert not any("exit_01" in a.known_exits and a.known_exits[0] != "exit_01" and len(a.known_exits) == 1 for a in s3)
    assert sim.signage == [("L1_office_n1", "exit_01", 1.0)]


def _assert_no_climb_to_dead_floor(sim: Sim) -> None:
    exit_lvs, elev = sim.world.nav.exit_levels, sim.world.nav.elev
    for t, aid, sid, a_lv, b_lv, action in sim.stair_moves:
        if action == "HELP":
            continue  # a helper may climb to fetch someone
        assert elev[b_lv] <= elev[a_lv] or b_lv in exit_lvs, (t, aid, sid, a_lv, b_lv)


def test_no_climbing_toward_floors_without_exits_school() -> None:
    occ = [{"where": {"room": r}, "count": 6, "personas": {"regular": 0.7, "visitor": 0.3}}
           for r in ("L1_class_n1", "L1_corridor", "L2_class_n2", "L2_class_s3")]
    ev = [{"id": "f", "type": "fire", "t_start_s": 0, "at": {"room": "L1_corridor"}, "params": {"block_after_s": 20}}]
    sim = _school(ev, occ, duration=240, tweak={"regular": {"premove_s": 5.0}, "visitor": {"premove_s": 10.0}})
    assert sim.stair_moves
    _assert_no_climb_to_dead_floor(sim)


TEPPER = Path(__file__).parents[1] / "samples/cmu/tepper/scene.json"


@pytest.mark.skipif(not TEPPER.exists(), reason="Tepper scene not generated")
def test_no_climbing_toward_floors_without_exits_tepper() -> None:
    scene = json.loads(TEPPER.read_text())
    occ = [{"where": {"room": r}, "count": 15, "personas": {"regular": 0.7, "visitor": 0.3}}
           for r in ("r_1042", "c_3_002", "r_5254")]
    sim = Sim(scene, {"duration_s": 150, "events": [], "occupants": occ}, 3)
    for a in sim.agents:
        a.traits["premove_s"] = 5.0
    assert all(sim.world.nav.elev[ex.lv] < max(sim.world.nav.elev) for ex in sim.world.exits.values())
    sim.run()
    assert sim.stair_moves
    _assert_no_climb_to_dead_floor(sim)


def test_branch_without_edit_reproduces_the_run() -> None:
    from sim.live import branch
    ev = [{"id": "f", "type": "fire", "t_start_s": 0, "at": {"room": "L1_office_n2"}},
          {"id": "r", "type": "rumor", "t_start_s": 20, "at": {"room": "L1_corridor"},
           "params": {"claim": {"exit_blocked": "exit_02"}}}]
    scen = _scenario(ev, duration=200)
    a = _run(scen, seed=11)
    b = branch(SCENE, scen, 11, a.records, t_branch=45.0, run_id="run")
    assert not b.done and b.tick == 90
    b.run()
    assert json.dumps(replay(a, "s", "c")) == json.dumps(replay(b, "s", "c"))


def test_branch_edit_adds_people_and_events_at_t() -> None:
    from sim.live import branch
    scen = _scenario([{"id": "a", "type": "alarm_only", "t_start_s": 0, "at": {"room": "L1_corridor"}}], duration=200)
    a = _run(scen, seed=11)
    b = branch(SCENE, scen, 11, a.records, 30.0,
               events=[{"id": "b", "type": "blocked_exit", "t_start_s": 0, "at": {"exit": "exit_01"}}],
               occupants=[{"where": {"room": "L1_office_s2"}, "count": 3, "personas": {"staff": 1.0},
                           "traits": {"speed_mps": 1.0}}])
    b.run()
    added = b.agents[len(a.agents):]
    assert len(added) == 3 and all(x.traits["speed_mps"] == 1.0 for x in added)
    assert [f["a"][:len(a.agents)] for f in a.frames[:60]] == [f["a"][:len(a.agents)] for f in b.frames[:60]]
    assert not [e for e in b.replay_events if e["kind"] == "exited" and e["ref"] == "exit_01" and e["t"] >= 30.0]


def test_async_decisions_fall_back_after_deadline() -> None:
    import time as _time
    from sim.live import AsyncDecisions
    from sim.policy import decide

    def slow(view: dict) -> dict:
        _time.sleep(0.2)
        return decide(view)

    sim = Sim(SCENE, _scenario([], duration=20), 1)
    sim.async_dec = AsyncDecisions(slow, workers=4)
    sim.run()  # unpaced: ticks take ~ms, so the 0.2 s answers miss the 4-tick deadline
    sim.async_dec.close()
    st = sim.async_dec.summary()
    assert st["fallback_deadline"] > 0
    assert any(r["validation"]["status"] == "fallback_deadline" for r in sim.records)


def test_live_server_streams_edits_and_writes_the_branch(tmp_path, monkeypatch) -> None:
    import struct
    from starlette.testclient import TestClient
    import sim.serve as serve
    monkeypatch.setattr(serve, "RUNS_DIR", tmp_path)
    with TestClient(serve.app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "start", "scene": "fixtures/tiny_office.scene.json",
                      "scenario": "sim/examples/tiny_office_fire.json", "seed": 1, "speed": 200})
        meta = ws.receive_json()
        assert meta["type"] == "meta" and len(meta["agents"]) == 30
        ws.send_json({"type": "edit", "occupants": [{"where": {"room": "L1_office_s2"}, "count": 2,
                                                       "personas": {"staff": 1.0}}]})
        n, got_ok = 0, False
        while n <= 30 or not got_ok:
            m = ws.receive()
            if m.get("bytes"):
                t, n = struct.unpack("<fI", m["bytes"][:8])
                assert len(m["bytes"]) == 8 + 10 * n
            elif json.loads(m["text"])["type"] == "edit_ok":
                got_ok = True
        ws.send_json({"type": "end"})
        while True:
            m = ws.receive()
            if m.get("text") and json.loads(m["text"])["type"] == "ended":
                break
    out = next(tmp_path.glob("*/branch_0"))
    assert (out / "replay.json").exists() and len(json.loads((out / "replay.json").read_text())["agents"]) == 32
