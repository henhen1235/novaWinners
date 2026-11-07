"""Hybrid occupants (cognition.playbook + cognition.hybrid) with a mocked LLM."""

import asyncio
import importlib
import json

import pytest

from cognition import hybrid, llm, playbook
from tests.test_cognition import fake_chat

D = importlib.import_module("cognition.decide")
CALLS: list[str] = []


def counting_chat(messages, **kw):
    CALLS.append(messages[1]["content"][:40])
    return fake_chat(messages)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    CALLS.clear()
    monkeypatch.setattr(llm, "chat", counting_chat)
    D.configure(offline=False, run_id="h")
    hybrid.STATS.update(playbook=0, llm=0, capped=0, replay=0, why={})
    yield


PB = playbook.clamp({"first_action": "finish_task", "premove_s": 50, "convinced_by": "crowd", "crowd_n": 3,
                     "exit_preference": "familiar", "official_trust": 0.8, "rumor_trust": 0.3,
                     "helps_others": False, "lines": ["Is this a drill?"]}, "regular")


def sim_view(aid="a_01", trigger="periodic", ref=None, tick=20, smoke=False, since=10.0, blocked_target=False):
    ps = [{"id": "p1", "kind": "alarm", "channel": "sound", "t_since_s": since}]
    if smoke:
        ps.append({"id": "p2", "kind": "smoke", "where": "r_003", "dist_m": 5.0, "channel": "sight"})
    beliefs = [{"id": "b1", "key": "exit_open:e_001", "value": False, "conf": 0.9,
                "source": {"kind": "observation"}, "tick": 5}] if blocked_target else []
    traits = {**playbook.to_traits(PB), "speed_mps": 1.3, "notice_s": 5, "t_since_cue_s": since,
              "activity": "CONTINUE_TASK", "state": "waiting"}
    return {"run_id": "h", "t_s": tick * 0.5, "tick": tick, "trigger": {"kind": trigger, "ref": ref},
            "agent": {"id": aid, "persona": "regular", "model": "google/gemini-2.5-flash-lite", "room": "r_002",
                      "level": "L1", "known_exits": ["e_001", "e_002"], "entry_exit": "e_001",
                      "target_exit": "e_001" if blocked_target else None, "target_stair": None, "traits": traits},
            "percepts": ps, "beliefs": beliefs,
            "options": [{"id": "o1", "action": "EVACUATE", "exit": "e_001", "route_m": 14.0, "queue_s": 2},
                        {"id": "o2", "action": "EVACUATE", "exit": "e_002", "route_m": 22.0, "queue_s": 0},
                        {"id": "o3", "action": "SEEK_INFO"}, {"id": "o4", "action": "CONTINUE_TASK"}]}


def test_playbook_is_clamped_to_persona_ranges():
    lo, hi = playbook.premove_range("regular")
    pb = playbook.clamp({"first_action": "panic", "premove_s": 99999, "convinced_by": "vibes", "crowd_n": 50,
                         "exit_preference": "window", "official_trust": 3, "rumor_trust": -1, "helps_others": "yes",
                         "lines": ["ok", "this is a very long line that goes on and on and on for ever", 7]},
                        "regular")
    assert pb["first_action"] in playbook.FIRST_ACTIONS and pb["convinced_by"] in playbook.CONVINCED
    assert lo <= pb["premove_s"] <= hi and pb["premove_s"] == pytest.approx(hi, abs=0.1)
    assert pb["crowd_n"] == 10 and pb["exit_preference"] == "familiar"
    assert pb["official_trust"] == 1.0 and pb["rumor_trust"] == 0.0 and pb["lines"] == ["ok"]
    assert playbook.clamp({"premove_s": 1}, "regular")["premove_s"] == pytest.approx(lo, abs=0.1)
    leave = playbook.clamp({"first_action": "leave", "premove_s": 200}, "regular")
    assert leave["premove_s"] <= 60
    assert playbook.clamp({"helps_others": True}, "mobility_impaired")["helps_others"] is False


def test_playbook_maps_onto_sim_traits():
    t = playbook.to_traits({**PB, "exit_preference": "signed"})
    assert t["premove_s"] == PB["premove_s"] and t["social_threshold"] == 3 and t["sign_follow_p"] == 0.9
    assert t["playbook"] == {**PB, "exit_preference": "signed"}
    assert playbook.to_traits({**PB, "exit_preference": "nearest"})["exit_familiarity"] == 0.8


@pytest.mark.parametrize("trig,ref", [("periodic", None), ("door_reached", "d_1"), ("level_reached", "L1"),
                                      ("premove_done", None), ("cue_noticed", "alarm")])
def test_routine_triggers_make_no_llm_call(trig, ref):
    r = hybrid.decide(sim_view(trigger=trig, ref=ref))
    assert not CALLS and r["record"]["source"] == "playbook"
    assert "Plan: finish_task" in r["record"]["decision"]["padm"]["why"]


@pytest.mark.parametrize("trig", ["hazard_seen", "heard", "rumor_heard", "path_blocked", "elevator_recalled"])
def test_surprise_triggers_call_the_llm(trig):
    r = hybrid.decide(sim_view(trigger=trig, smoke=True))
    assert len(CALLS) == 1 and r["record"]["source"] == "llm" and r["record"]["escalation"] == trig


def test_new_cue_escalates_once_then_is_routine():
    first = hybrid.decide(sim_view(trigger="periodic", smoke=True, tick=20))
    assert first["record"]["escalation"].startswith("new cue") and len(CALLS) == 1
    again = hybrid.decide(sim_view(trigger="periodic", smoke=True, tick=40))
    assert again["record"]["source"] == "playbook" and len(CALLS) == 1


def test_blocked_route_escalates():
    r = hybrid.decide(sim_view(trigger="door_reached", blocked_target=True))
    assert r["record"]["source"] == "llm" and "e_001" in r["record"]["escalation"]


def test_cost_cap_stops_new_calls(monkeypatch):
    monkeypatch.setenv("NOVA_COST_CAP", "0")
    r = hybrid.decide(sim_view(trigger="heard", smoke=True))
    assert not CALLS and r["record"]["source"] == "playbook" and "cost cap" in r["record"]["validation"]["errors"][0]


def test_playbook_prompt_reaches_the_llm():
    hybrid.decide(sim_view(trigger="heard"))
    inp = D.build_inputs(sim_view(trigger="heard", aid="a_02"))
    user = D.prompt_for(inp, D.compute_state(inp))[1]["content"]
    assert "YOUR PLAN" in user and "'playbook'" not in user and "first_action" not in user


def test_replay_is_deterministic(tmp_path, monkeypatch):
    views = [sim_view(aid=f"a_{i}", trigger=t, tick=10 + i, smoke=i % 2 == 0)
             for i, t in enumerate(["periodic", "heard", "door_reached", "hazard_seen"])]
    first = [hybrid.decide(v)["record"] for v in views]
    log = tmp_path / "decisions.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in first))
    monkeypatch.setattr(llm, "chat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no calls on replay")))
    D.configure(replay=log, run_id="h")
    assert [hybrid.decide(v)["record"] for v in views] == first


def test_playbooks_are_recorded_and_reused(tmp_path, monkeypatch):
    scen = {"seed": 1, "scene": "x.json", "duration_s": 60, "events": [], "assumptions": ["test office"],
            "occupants": [{"where": {"room": "r_1"}, "count": 3, "personas": {"regular": 1.0}},
                          {"where": {"room": "r_2"}, "count": 2, "personas": {"staff": 1.0}}]}
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(scen))
    calls = []

    async def fake_achat(client, messages, **kw):
        calls.append(1)
        pids = [ln.split(":")[0][2:] for ln in messages[1]["content"].splitlines() if ln.startswith("- p")]
        return json.dumps({"playbooks": [{"pid": p, "first_action": "leave", "premove_s": 5, "crowd_n": 2,
                                          "convinced_by": "alarm", "exit_preference": "nearest",
                                          "official_trust": 0.9, "rumor_trust": 0.2, "helps_others": True,
                                          "lines": ["Everyone out"]} for p in pids]}), \
            {"cached": False, "latency_ms": 3, "cost_usd": 0.001}
    monkeypatch.setattr(llm, "achat", fake_achat)
    out = tmp_path / "run"
    s1 = playbook.build(sp, out)
    rows = [json.loads(x) for x in (out / "playbooks.jsonl").read_text().splitlines()]
    hyb = json.loads((out / "scenario_hybrid.json").read_text())
    assert s1["calls"] == 1 and len(rows) == 5 and all(r["source"] == "llm" for r in rows)
    assert [o["count"] for o in hyb["occupants"]] == [1] * 5 and hyb["occupants"][3]["where"] == {"room": "r_2"}
    assert hyb["occupants"][0]["traits"]["playbook"]["exit_preference"] == "nearest"
    assert {r["agent_id"] for r in rows} == {f"a_0{i}" for i in range(1, 6)}
    monkeypatch.setattr(llm, "achat", None)  # a second build must not call
    s2 = playbook.build(sp, out)
    assert s2["reused"] and json.loads((out / "scenario_hybrid.json").read_text()) == hyb
