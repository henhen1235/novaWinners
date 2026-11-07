"""flyagent: connectome-table fruit fly as an occupant decider (research/FLY_AGENT.md)."""

import copy
import time

from flyagent import decide
from flyagent.core import key, table
from flyagent.decide import motor_command, primitive

LEFT, RIGHT, AHEAD = 90.0, -90.0, 0.0


def _view(percepts, model="fruit_fly", options=None):
    return {
        "t_s": 30.0, "tick": 60, "trigger": {"kind": "hazard_seen", "ref": "ev1"},
        "agent": {"id": "a_01", "persona": "regular", "model": model, "pos": [0.0, 0.0], "room": "r1",
                  "known_exits": ["eL", "eR", "eA"], "entry_exit": "eA", "target_exit": None,
                  "traits": {"speed_mps": 1.3}},
        "percepts": [{"id": f"p{i + 1}", **p} for i, p in enumerate(percepts)],
        "beliefs": [],
        "options": options or [
            {"id": "o1", "action": "EVACUATE", "exit": "eL", "route_m": 10.0, "bearing_deg": LEFT},
            {"id": "o2", "action": "EVACUATE", "exit": "eR", "route_m": 10.0, "bearing_deg": RIGHT},
            {"id": "o3", "action": "EVACUATE", "exit": "eA", "route_m": 10.0, "bearing_deg": AHEAD},
            {"id": "o4", "action": "SEEK_INFO"}, {"id": "o5", "action": "CONTINUE_TASK"}],
    }


SMOKE_LEFT = [{"kind": "alarm", "channel": "sound"},
              {"kind": "smoke", "where": "r2", "dist_m": 3.0, "bearing_deg": LEFT}]
VIEWS = [
    [],
    [{"kind": "alarm", "channel": "sound"}],
    SMOKE_LEFT,
    [{"kind": "fire", "where": "r2", "dist_m": 8.0, "bearing_deg": RIGHT}],
    [{"kind": "people_moving", "count": 5, "toward": "eA"}],
    [{"kind": "exit_sign", "ref": "eL", "dist_m": 4.0, "bearing_deg": LEFT}],
    [{"kind": "exit_sign", "ref": "eR", "dist_m": 4.0, "bearing_deg": RIGHT},
     {"kind": "smoke", "where": "r2", "dist_m": 9.0, "bearing_deg": LEFT}],
]


def _primitive(k, wiring):
    t = table()
    return primitive(motor_command(t["entries"][k][wiring], t["baseline"][wiring]))


def test_choice_is_offered_and_record_has_provenance():
    for per in VIEWS:
        for model in ("fruit_fly", "fruit_fly_scrambled"):
            v = _view(per, model)
            res = decide(v)
            assert res["choice"] in {o["id"] for o in v["options"]}
            fly = res["record"]["fly"]
            assert fly["table_key"] in table()["entries"]
            assert fly["provenance"]["connectome"].startswith("malecns")


def test_deterministic():
    for per in VIEWS:
        a, b = decide(_view(per)), decide(_view(per))
        for r in (a, b):
            r["record"].pop("latency_ms")
        assert a == b


def test_decide_does_not_mutate_view():
    v = _view(SMOKE_LEFT)
    before = copy.deepcopy(v)
    decide(v)
    assert v == before


def test_smoke_on_left_follows_the_table():
    res = decide(_view(SMOKE_LEFT))
    fly = res["record"]["fly"]
    assert fly["table_key"] == key({"smoke": ("L", 2), "sound": ("B", 1)})
    prim = _primitive(fly["table_key"], "real")
    assert fly["primitive"] == prim
    bearing = {"o1": LEFT, "o2": RIGHT, "o3": AHEAD}.get(res["choice"])
    if prim == "freeze":
        assert res["choice"] == "o5"
    elif prim == "turn_left":
        assert bearing is not None and bearing >= 0
    elif prim == "turn_right":
        assert bearing is not None and bearing <= 0
    elif prim == "forward":
        assert res["choice"] == "o3"
    else:  # escape / backward: away from the threat on the left
        assert res["choice"] == "o2"


def test_scrambled_wiring_differs_from_real():
    keys = list(table()["entries"])
    differ = [k for k in keys if _primitive(k, "real") != _primitive(k, "scrambled")]
    assert differ, "scrambled wiring gives the same primitive for every stimulus"
    choices = [(decide(_view(p))["choice"], decide(_view(p, "fruit_fly_scrambled"))["choice"]) for p in VIEWS]
    assert any(a != b for a, b in choices)


def test_fast_enough():
    views = [_view(p) for p in VIEWS] * 30
    t0 = time.perf_counter()
    for v in views:
        decide(v)
    assert (time.perf_counter() - t0) / len(views) * 1000 < 5.0
