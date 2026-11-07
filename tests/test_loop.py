"""loop/: adversary output validates (with repair), optimizer patches apply and validate,
scoreboard computed, rerun replays decisions to identical results. LLMs are faked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loop import patch
from loop.adversary import attack
from loop.run_loop import run

REPO = Path(__file__).resolve().parents[1]
SCENE_PATH = "fixtures/tiny_office.scene.json"
SCENE = json.loads((REPO / SCENE_PATH).read_text())
BASE = {"schema_version": "0.1", "scene": SCENE_PATH, "seed": 42, "duration_s": 120,
        "events": [{"id": "ev_alarm", "type": "alarm_only", "t_start_s": 0, "at": {"room": "L1_corridor"}, "params": {}}],
        "occupants": [{"where": {"room": "L1_office_n1"}, "count": 4, "personas": {"regular": 1.0}},
                      {"where": {"room": "L1_office_s2"}, "count": 4, "personas": {"regular": 0.5, "visitor": 0.5}}]}


def _call(name: str, args: dict, i: int) -> dict:
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def fake_llm(messages: list[dict], tools: list[dict], tool_choice: str, model: str) -> tuple[dict, float]:
    """Adversary: a bad exit id first (must be repaired), then a valid attack. Optimizer: two fixes."""
    names = {t["function"]["name"] for t in tools}
    turn = sum(m["role"] == "assistant" for m in messages)
    if "block_exit" in names:
        script = [[_call("block_exit", {"exit": "exit_99", "t_start_s": 0, "cause": "locked", "why": "21 of 30 use it"}, 0)],
                  [_call("block_exit", {"exit": "exit_01", "t_start_s": 0, "cause": "locked", "why": "21 of 30 use it"}, 1),
                   _call("spread_rumor", {"room": "L1_office_s2", "t_start_s": 10, "text": "Main Entrance is blocked!",
                                          "claim_exit_blocked": "exit_02", "why": "exit_02 carries 9 of 30"}, 2)],
                  [_call("submit", {"summary": "done"}, 3)]]
    else:
        script = [[_call("unlock_exit", {"exit": "exit_01", "why": "8 stragglers"}, 0),
                   _call("pa_announcement", {"t_s": 20, "text": "Main Entrance is open, leave now.", "exit_open": "exit_02",
                                             "why": "4 misled"}, 1)],
                  [_call("submit", {"summary": "done"}, 2)]]
    return {"role": "assistant", "tool_calls": script[min(turn, len(script) - 1)]}, 0.001


def test_adversary_repairs_and_validates():
    res = attack(SCENE, {}, BASE, {"exit_usage": {"exit_01": 6, "exit_02": 2}, "total": 8}, 1, llm=fake_llm)
    assert res["source"] == "llm" and len(res["injections"]) == 2
    tool_msgs = [json.loads(m["content"]) for m in res["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["ok"] is False and "unknown exit" in tool_msgs[0]["errors"][0]
    assert patch.validate_scenario(res["scenario"], SCENE) == []


def test_adversary_heuristic_cites_numbers():
    res = attack(SCENE, {}, BASE, {"exit_usage": {"exit_01": 6, "exit_02": 2}, "total": 8}, 1, no_llm=True)
    assert [i["event"]["type"] for i in res["injections"]] == ["blocked_exit", "rumor"]
    assert res["injections"][0]["event"]["at"] == {"exit": "exit_01"}
    assert all(any(c.isdigit() for c in i["why"]) for i in res["injections"])


@pytest.mark.parametrize("iv", [
    {"kind": "add_warden", "args": {"room": "L1_office_n1", "count": 1}},
    {"kind": "add_signage", "args": {"room": "L1_corridor", "exit": "exit_02"}},
    {"kind": "pa_announcement", "args": {"t_s": 30, "text": "Main Entrance is open.", "exit_open": "exit_02"}},
    {"kind": "unlock_exit", "args": {"exit": "exit_01"}},
    {"kind": "assign_familiar_exit", "args": {"room": "L1_office_s2", "exit": "exit_02"}},
    {"kind": "widen_door", "args": {"door": "door_05", "width_m": 1.2}},
    {"kind": "assisted_evacuation_plan", "args": {}},
])
def test_interventions_apply_and_validate(iv):
    sc = {**BASE, "events": BASE["events"] + [{"id": "b", "type": "blocked_exit", "t_start_s": 0,
                                                "at": {"exit": "exit_01"}, "params": {"cause": "locked"}}]}
    scene, new, errs = patch.try_apply({"id": "fix1_1", "why": "x", **iv}, SCENE, sc)
    assert errs == []
    assert (new, scene) != (sc, SCENE)
    assert patch.validate_scenario(new, scene) == []
    assert sc["events"][-1]["id"] == "b" and len(sc["occupants"]) == 2  # inputs untouched


@pytest.mark.parametrize("iv", [
    {"kind": "widen_door", "args": {"door": "door_05", "width_m": 2.9}},
    {"kind": "unlock_exit", "args": {"exit": "exit_02"}},
    {"kind": "add_warden", "args": {"room": "nowhere"}},
    {"kind": "build_new_exit", "args": {}},
])
def test_interventions_rejected(iv):
    _, _, errs = patch.try_apply({"id": "f", "why": "x", **iv}, SCENE, BASE)
    assert errs


def test_loop_scoreboard_and_replay(tmp_path, monkeypatch):
    import cognition.llm

    def no_network(*a, **k):
        raise RuntimeError("offline test")
    monkeypatch.setattr(cognition.llm, "chat", no_network)  # occupants fall back to rules, still recorded
    scen = tmp_path / "base.json"
    scen.write_text(json.dumps(BASE))
    out = tmp_path / "loop"
    a = run(str(REPO / SCENE_PATH), str(scen), str(out), rounds=1, decide="cognition", no_llm=False,
            seeds=(42,), llm=fake_llm)
    assert [s["stage"] for s in a["stages"]] == ["r0_baseline", "r1_attack", "r1_fixed"]
    attacked, fixed = a["stages"][1]["median"], a["stages"][2]["median"]
    assert attacked["total"] == fixed["total"] == 8
    assert a["rounds"][0]["optimizer"]["source"] == "llm" and len(a["rounds"][0]["optimizer"]["interventions"]) == 2
    assert set(a["rounds"][0]["improvement"]) == {"t95_s", "t90_s", "stragglers", "misled_by_rumor", "max_queue"}
    assert (out / "r1_attack" / "s42" / "decisions.jsonl").exists() and (out / "report.md").exists()
    first = (out / "r1_fixed" / "s42" / "replay.json").read_text()
    b = run(str(REPO / SCENE_PATH), str(scen), str(out), rounds=1, decide="cognition", seeds=(42,), llm=fake_llm)
    assert (out / "r1_fixed" / "s42" / "replay.json").read_text() == first
    assert [s["median"] for s in b["stages"]] == [s["median"] for s in a["stages"]]
