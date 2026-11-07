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


def test_sim_extras_and_interventions_validate():
    data = _with(
        events=[{"id": "b", "type": "blocked_exit", "t_start_s": 0, "at": {"exit": "exit_01"},
                 "params": {"cause": "locked"}},
                {"id": "o", "type": "open_exit", "t_start_s": 30, "at": {"exit": "exit_01"}},
                {"id": "pa", "type": "pa_announcement", "t_start_s": 20, "at": {"room": "L1_corridor"},
                 "params": {"text": "West exit is open", "claim": {"exit_open": "exit_01"}}}],
        occupants=[{"where": {"room": "L1_office_n1"}, "count": 2, "familiar_exit": "exit_02"},
                   {"where": {"room": "L1_corridor"}, "count": 1, "personas": {"staff": 1.0}}],
        interventions=[{"type": "signage", "exit": "exit_01", "p": 0.8},
                       {"type": "signage", "exit": "exit_02", "p": 0.5, "room": "L1_office_n3"},
                       {"type": "warden", "room": "L1_corridor", "count": 2},
                       {"type": "widen_door", "door": "door_01", "width_m": 1.2}])
    assert validate(data, SCENE) == []
    assert normalize(data)["occupants"][0]["familiar_exit"] == "exit_02"


def test_stair_targets_validate_on_school():
    data = _with(scene="fixtures/two_floor_school.scene.json", occupants=[],
                 events=[{"id": "s", "type": "blocked_exit", "t_start_s": 0, "at": {"stair": "stair_01"}},
                         {"id": "w", "type": "blocked_exit", "t_start_s": 0, "at": {"room": "L2_stair_e"}},
                         {"id": "f", "type": "smoke", "t_start_s": 0, "at": {"stair": "stair_02"}}])
    assert validate(data, SCHOOL) == []
    data["events"][0]["at"] = {"stair": "stair_09"}
    assert any("unknown stair 'stair_09'" in e for e in validate(data, SCHOOL))


@pytest.mark.parametrize("change, expected", [
    ({"events": [{"id": "o", "type": "open_exit", "t_start_s": 0, "at": {"room": "L1_corridor"}}]},
     "open_exit needs"),
    ({"events": [{"id": "pa", "type": "pa_announcement", "t_start_s": 0, "at": {"room": "L1_corridor"},
                  "params": {"claim": {"exit_open": "exit_09"}}}]}, "unknown exit 'exit_09'"),
    ({"events": [{"id": "pa", "type": "pa_announcement", "t_start_s": 0, "at": {"room": "L1_corridor"}}]},
     "needs the message text"),
    ({"occupants": [{"where": {"room": "L1_office_n1"}, "count": 1, "familiar_exit": "exit_09"}]},
     "familiar_exit: unknown exit"),
    ({"interventions": [{"type": "signage", "exit": "exit_09"}]}, "interventions[0].exit: unknown exit"),
    ({"interventions": [{"type": "signage", "exit": "exit_01", "p": 1.5}]}, "less than or equal to 1"),
    ({"interventions": [{"type": "warden", "room": "L1_corridor", "count": 3}]}, "less than or equal to 2"),
    ({"interventions": [{"type": "widen_door", "door": "door_01", "width_m": 0.5}]}, "narrower than door"),
    ({"interventions": [{"type": "widen_door", "door": "door_01", "width_m": 3.0}]}, "less than or equal to 2.4"),
    ({"interventions": [{"type": "teleport"}]}, "interventions.0"),
])
def test_sim_extras_errors(change, expected):
    errors = validate(_with(**change), SCENE)
    assert any(expected in e for e in errors), errors


def test_assisted_evacuation_plan_and_param():
    plain = _with(interventions=[{"type": "assisted_evacuation_plan"}], params={"assisted_evacuation": True})
    assert validate(plain, SCENE) == []
    # loop/patch.py compares entries to this exact dict, so normalize must not add keys.
    assert normalize(plain)["interventions"] == [{"type": "assisted_evacuation_plan"}]
    assert validate(_with(interventions=[{"type": "assisted_evacuation_plan", "staff_per_person": 2}]), SCENE) == []
    bad = validate(_with(interventions=[{"type": "assisted_evacuation_plan", "staff_per_person": 3}],
                         params={"assisted_evacuation": "yes"}), SCENE)
    assert any("staff_per_person" in e for e in bad) and any("params.assisted_evacuation" in e for e in bad), bad


VIEWER_TRAITS = {"speed_mps": 1.25, "exit_familiarity": 0.5, "sign_follow_p": 0.6, "risk_tolerance": 0.5,
                 "ocean": {"O": 0.2, "C": -0.4, "E": 0.0, "A": 1.0, "N": -1.0}}


def test_viewer_placed_people_validate():
    """Shapes blueprint/viewer/src/edit.ts sends: one group per clicked point, or a dragged box."""
    data = _with(occupants=[
        {"where": {"point": [1.0, 1.0], "level": "L1"}, "count": 1, "personas": {"fruit_fly": 1.0},
         "traits": VIEWER_TRAITS},
        {"where": {"point": [8.0, 5.0], "level": "L1"}, "count": 1, "personas": {"fruit_fly_scrambled": 1.0},
         "traits": {**VIEWER_TRAITS, "social_threshold": 0.3, "ocean": [0.1, 0.2, 0.3, 0.4, 0.5]}},
        {"where": {"box": [[4.0, 9.0], [1.0, 6.5]], "level": "L1"}, "count": 8, "personas": {"older_adult": 1.0}}])
    assert validate(data, SCENE) == []
    out = normalize(data)
    assert out["occupants"][0]["traits"]["ocean"]["A"] == 1.0
    assert out["occupants"][2]["where"] == {"box": [[4.0, 9.0], [1.0, 6.5]], "level": "L1"}


def test_point_and_box_groups_are_not_spread_by_chat():
    groups = [{"where": {"point": [1.0, 1.0], "level": "L1"}, "count": 1},
              {"where": {"box": [[1.0, 6.5], [4.0, 9.0]], "level": "L1"}, "count": 3}]
    assert chat.expand_occupants(groups, SCENE) == groups


@pytest.mark.parametrize("group, expected", [
    ({"where": {"point": [2.5, 2.0], "level": "L1"}, "count": 1}, "not inside walkable"),  # the desk in Office N1
    ({"where": {"point": [1.0, 1.0], "level": "L9"}, "count": 1}, "unknown level 'L9'"),
    ({"where": {"point": [1.0, 1.0]}, "count": 1}, "need a level"),
    ({"where": {"room": "L1_office_n1", "level": "L1"}, "count": 1}, "room takes none"),
    ({"where": {"room": "L1_office_n1", "point": [1.0, 1.0], "level": "L1"}, "count": 1}, "exactly one of room, point, box"),
    ({"where": {"box": [[40.0, 40.0], [50.0, 50.0]], "level": "L1"}, "count": 1}, "no walkable area"),
    ({"where": {"box": [[1.0, 1.0], [1.5, 1.5]], "level": "L1"}, "count": 50}, "holds at most"),
    ({"where": {"room": "L1_office_n1"}, "count": 1, "traits": {"speed_mps": 3.0}}, "traits.speed_mps"),
    ({"where": {"room": "L1_office_n1"}, "count": 1, "traits": {"risk_tolerance": 1.5}}, "traits.risk_tolerance"),
    ({"where": {"room": "L1_office_n1"}, "count": 1, "traits": {"ocean": {"O": 2.0}}}, "traits.ocean"),
    ({"where": {"room": "L1_office_n1"}, "count": 1, "traits": {"ocean": [0.1, 0.2]}}, "traits.ocean"),
    ({"where": {"room": "L1_office_n1"}, "count": 1, "personas": {"fruit_bat": 1.0}}, "unknown persona 'fruit_bat'"),
])
def test_viewer_people_errors(group, expected):
    errors = validate(_with(occupants=[group]), SCENE)
    assert any(expected in e for e in errors), errors


def test_points_count_against_their_room_cap():
    groups = [{"where": {"point": [1.0, 1.0], "level": "L1"}, "count": 20}] * 2  # 40 > 37 for Office N1
    assert any("holds at most 37" in e for e in validate(_with(occupants=groups), SCENE))
