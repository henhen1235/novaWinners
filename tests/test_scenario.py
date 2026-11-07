import json
from pathlib import Path

import pytest

from scenario import chat, seed as seed_mod
from scenario.resolve import resolve_location
from scenario.schema import normalize, validate

ROOT = Path(__file__).resolve().parents[1]
SCENE = json.loads((ROOT / "fixtures" / "tiny_office.scene.json").read_text())
SCHOOL = json.loads((ROOT / "fixtures" / "two_floor_school.scene.json").read_text())

# The example from docs/SIM_INTERFACES.md, with ids from tiny_office.
EXAMPLE = {"schema_version": "0.1", "scene": "fixtures/tiny_office.scene.json", "seed": 42, "duration_s": 300,
           "events": [{"id": "ev1", "type": "fire", "t_start_s": 0, "at": {"room": "L1_office_n2"}, "params": {}}],
           "occupants": [{"where": {"room": "L1_office_n1"}, "count": 10,
                          "personas": {"regular": 0.6, "visitor": 0.3, "staff": 0.1},
                          "models": {"google/gemini-2.5-flash-lite": 1.0}}],
           "assumptions": []}


def _with(**changes):
    return {**json.loads(json.dumps(EXAMPLE)), **changes}


def test_interface_example_is_valid():
    assert validate(EXAMPLE, SCENE) == []


@pytest.mark.parametrize("change, expected", [
    ({"events": [{"id": "e", "type": "fire", "t_start_s": 0, "at": {"room": "kitchen"}}]}, "unknown room 'kitchen'"),
    ({"events": [{"id": "e", "type": "fire", "t_start_s": 0, "at": {"room": "L1_corridor", "exit": "exit_01"}}]},
     "exactly one of"),
    ({"events": [{"id": "e", "type": "smoke", "t_start_s": 0, "at": {"point": [50.0, 50.0], "level": "L1"}}]},
     "not inside walkable"),
    ({"events": [{"id": "e", "type": "blocked_exit", "t_start_s": 0, "at": {"room": "L1_corridor"}}]},
     "blocked_exit needs"),
    ({"events": [{"id": "e", "type": "rumor", "t_start_s": 10, "at": {"room": "L1_corridor"},
                  "params": {"text": "west exit is blocked", "claim": {"exit_blocked": "exit_09"}}}]},
     "unknown exit 'exit_09'"),
    ({"events": [{"id": "e", "type": "alarm_only", "t_start_s": 900, "at": {"room": "L1_corridor"}}]},
     "after duration_s"),
    ({"occupants": [{"where": {"room": "L1_office_n1"}, "count": 100}]}, "holds at most 37"),
    ({"occupants": [{"where": {"room": "L1_office_n1"}, "count": 3, "personas": {"regular_employee": 0.5}}]},
     "must sum to 1"),
    ({"occupants": [{"where": {"room": "L1_office_n1"}, "count": 3, "personas": {"ninja": 1.0}}]},
     "unknown persona 'ninja'"),
])
def test_validation_errors_name_the_problem(change, expected):
    errors = validate(_with(**change), SCENE)
    assert any(expected in e for e in errors), errors


def test_point_inside_walkable_is_valid_and_defaults_fill_in():
    data = _with(events=[{"id": "e", "type": "smoke", "t_start_s": 5, "at": {"point": [8.0, 5.0], "level": "L1"}}],
                 occupants=[{"where": {"room": "L1_office_s1"}, "count": 4}])
    assert validate(data, SCENE) == []
    out = normalize(data)
    assert out["events"][0]["at"] == {"point": [8.0, 5.0], "level": "L1"}
    assert abs(sum(out["occupants"][0]["personas"].values()) - 1) < 1e-9


@pytest.mark.parametrize("scene, text, kind, want", [
    (SCENE, "the corridor", "any", "L1_corridor"),
    (SCENE, "Main Entrance", "any", "exit_02"),
    (SCENE, "east exit", "exit", "exit_02"),
    (SCENE, "office n3", "any", "L1_office_n3"),
    (SCENE, "door_05", "any", "door_05"),
    (SCHOOL, "floor 2", "level", "L2"),
])
def test_resolve_location_finds_one_clear_match(scene, text, kind, want):
    got = resolve_location(scene, text, kind)
    assert not got["ambiguous"] and got["candidates"][0]["id"] == want


@pytest.mark.parametrize("text", ["office", "kitchen", "stairs"])
def test_resolve_location_flags_ambiguous_or_missing(text):
    scene = SCHOOL if text == "stairs" else SCENE
    assert resolve_location(scene, text)["ambiguous"]


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_full_tool_loop_resolves_asks_repairs_and_submits():
    """Scripted LLM: resolve an ambiguous place, ask the user, submit a bad scenario, then fix it."""
    bad = {"duration_s": 300, "assumptions": [], "events": [
        {"id": "f", "type": "fire", "t_start_s": 0, "at": {"room": "office"}}],
        "occupants": [{"where": {"all": True}, "count": 10}]}
    good = {**bad, "events": [{"id": "f", "type": "fire", "t_start_s": 0, "at": {"room": "L1_office_n2"}},
                              {"id": "r", "type": "rumor", "t_start_s": 40, "at": {"room": "L1_corridor"},
                               "params": {"text": "Main entrance is blocked", "claim": {"exit_blocked": "exit_02"}}}],
            "assumptions": ["user picked Office N2"]}
    script = [
        {"role": "assistant", "tool_calls": [_call(1, "resolve_location", {"text": "office", "kind": "room"})]},
        {"role": "assistant", "tool_calls": [_call(2, "ask_user", {"question": "Which office?",
                                                                   "options": ["Office N1", "Office N2"]})]},
        {"role": "assistant", "tool_calls": [_call(3, "submit_scenario", bad)]},
        {"role": "assistant", "tool_calls": [_call(4, "submit_scenario", good)]},
    ]
    seen = []

    def fake_llm(messages, tools, tool_choice):
        seen.append(messages[-1])
        return script[len(seen) - 1], 0.001

    asked = []
    scenario, cost, messages = chat.run(SCENE, "fixtures/tiny_office.scene.json", "fire in the office, 10 people",
                                        lambda q, o: asked.append((q, o)) or "Office N2", llm=fake_llm)
    assert asked == [("Which office?", ["Office N1", "Office N2"])]
    resolved = json.loads(seen[1]["content"])
    assert resolved["ambiguous"] and len(resolved["candidates"]) == 6
    repair = json.loads(seen[3]["content"])
    assert repair["ok"] is False and any("unknown room 'office'" in e for e in repair["errors"])
    assert scenario is not None and validate(scenario, SCENE) == []
    assert sum(o["count"] for o in scenario["occupants"]) == 10  # {"all": true} spread over the offices
    assert {o["where"]["room"] for o in scenario["occupants"]} <= {r["id"] for r in SCENE["rooms"]}
    assert cost == pytest.approx(0.004)


def test_one_shot_answer_takes_first_option():
    assert "Office N1" in chat.one_shot_answer("Which office?", ["Office N1", "Office N2"])


def test_seed_clamps_llm_counts_to_occupant_load(monkeypatch):
    reply = {"rooms": [{"id": "L1_office_n1", "count": 50, "personas": {"warden": 2, "ninja": 5}},
                       {"id": "L1_office_n2", "count": 1, "personas": {"regular_employee": 1}}]}
    monkeypatch.setattr(seed_mod, "complete", lambda prompt, **kw: json.dumps(reply))
    occupants, notes = seed_mod.seed(SCENE)
    rows = {o["where"]["room"]: o for o in occupants}
    assert rows["L1_office_n1"]["count"] == 2  # IBC load of an 18.7 m2 office
    assert rows["L1_office_n1"]["personas"] == {"warden": 1.0}
    assert rows["L1_office_n2"]["count"] == 1
    assert "L1_corridor" not in rows
    assert validate({"scene": "x", "occupants": occupants}, SCENE) == []


def test_seed_without_llm_uses_half_the_load():
    occupants, notes = seed_mod.seed(SCENE, use_llm=False)
    assert sum(o["count"] for o in occupants) == 6
