"""loop.advise with a faked LLM: evidence ids, id validation with one repair, patches, verification, report."""

from __future__ import annotations

import json
from pathlib import Path

from loop import advise
from loop.simrun import run_one

REPO = Path(__file__).resolve().parents[1]
SCENE_PATH = str(REPO / "fixtures/tiny_office.scene.json")
SCENE = json.loads(Path(SCENE_PATH).read_text())
BASE = {"schema_version": "0.1", "scene": SCENE_PATH, "seed": 42, "duration_s": 90,
        "events": [{"id": "ev_fire", "type": "fire", "t_start_s": 0, "at": {"room": "L1_office_n2"}, "params": {}}],
        "occupants": [{"where": {"room": "L1_office_n1"}, "count": 4, "personas": {"regular": 0.5, "skeptic": 0.5}},
                      {"where": {"room": "L1_office_s2"}, "count": 4, "personas": {"regular": 1.0}}]}


def _call(name: str, args: dict, i: int) -> dict:
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def fake_llm(messages: list[dict], tools: list[dict], model: str) -> tuple[dict, float]:
    """Turn 1: findings with a bad id plus valid recommendations; turn 2: repaired findings."""
    turn = sum(m["role"] == "assistant" for m in messages)
    rec = {"title": "Sign the west exit", "kind": "signage", "audience": "operations", "refs": ["exit_01", "L1_office_n1"],
           "evidence": ["E1"], "basis": "P5", "why": "x", "expected_effect": "y", "effort": "low"}
    layout = {**rec, "title": "Move the copier", "kind": "layout", "audience": "design", "refs": ["L1_corridor"],
              "effort": "high"}
    if turn == 0:
        return {"role": "assistant", "tool_calls": [
            _call("submit_findings", {"summary": "s", "findings": [{"title": "t", "detail": "d", "refs": ["nowhere"],
                                                                   "evidence": ["E1"]}]}, 0),
            _call("submit_recommendations", {"recommendations": [rec, layout]}, 1)]}, 0.01
    return {"role": "assistant", "tool_calls": [
        _call("submit_findings", {"summary": "s", "findings": [{"title": "t", "detail": "d", "refs": ["L1_corridor"],
                                                               "evidence": ["E1", "E99"]}]}, 2)]}, 0.01


def test_advise_end_to_end(tmp_path):
    scen = tmp_path / "s.json"
    scen.write_text(json.dumps(BASE))
    run = tmp_path / "run"
    run_one(SCENE, BASE, 42, run, "policy", True)
    out = run / "recommendations.json"
    body = advise.advise(run, SCENE_PATH, str(scen), out, n_seeds=2, llm=fake_llm)
    assert [e["id"] for e in body["evidence"]][:2] == ["E1", "E2"]
    assert any("Screening, not a code review" in e["text"] for e in body["evidence"])
    assert body["findings"] == []  # bad on both tries (unknown id, then unknown evidence) -> dropped
    kinds = {r["kind"]: r for r in body["recommendations"]}
    assert kinds["signage"]["verified"]["simulated"] and kinds["signage"]["verified"]["seeds"] == 2
    assert set(kinds["signage"]["verified"]["after"]) >= {"evacuated", "total", "t90_s", "t_last_s", "stragglers"}
    assert kinds["layout"]["verified"]["simulated"] is False
    assert body["cost_usd"] == 0.02
    md = (run / "report.md").read_text()
    assert "For the safety team" in md and "For the architect" in md and "not simulated" in md


def test_add_exit_and_widen_patches():
    cands = advise.exit_candidates(SCENE)
    for c in cands:
        p = advise.to_patch({"kind": "add_exit", "refs": [c["door"]]}, SCENE, BASE, cands)
        assert p is not None and len(p[0]["exits"]) == len(SCENE["exits"]) + 1
    p = advise.to_patch({"kind": "widen_door", "refs": ["door_05"]}, SCENE, BASE, cands)
    assert next(d for d in p[0]["doors"] if d["id"] == "door_05")["width_m"] > 0.9
    assert advise.to_patch({"kind": "add_exit", "refs": ["door_99"]}, SCENE, BASE, cands) is None
    assert advise.to_patch({"kind": "refuge", "refs": []}, SCENE, BASE, cands) is None
