"""decide(view) for fruit-fly occupants: percepts -> sensory neurons -> connectome table -> DNs -> option.

A toy embodiment (research/FLY_AGENT.md): the fly gets egocentric left/right stimuli, the
precomputed Shiu-LIF response of the MaleCNS connectome is looked up, the descending-neuron
rates become one motor primitive, and the primitive picks the offered option whose bearing
matches it. Occupants that are not flies go to the sim's rule-based policy.

Bearings: positive = to the fly's left, degrees, relative to its heading (toward its target
exit, else the exit it came in by). Taken from `bearing_deg` on a percept or option when the
sim provides it, otherwise computed from scene.json positions (straight line, walls ignored).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from functools import lru_cache
from pathlib import Path

from flyagent.core import key, table

FLY_MODELS = {"fruit_fly": "real", "fruit_fly_scrambled": "scrambled"}
SIDE_DEG = 15.0          # |bearing| below this (or above 180 - this) counts as both eyes/antennae
NEAR_SMOKE_M = 5.0       # smoke closer than this is the strong level
LOOM_MIN_PEOPLE = 3      # this many people moving in view counts as a looming crowd
GF_ESCAPE_HZ = 50.0      # giant-fiber rate that counts as an escape takeoff
MDN_BACK_HZ = 10.0       # moonwalker rate that counts as backing up
FREEZE_RATIO = 0.4       # forward drive below this share of baseline = freeze
TURN_MIN_HZ = 5.0        # DNa asymmetry (vs baseline) below this = no turn
TURN_FULL_HZ = 30.0      # DNa asymmetry for a 90 degree turn (fly4 loop.py TURN_HZ)
STAY_ACTIONS = ("CONTINUE_TASK", "WAIT", "HIDE")


def decide(view: dict) -> dict:
    a = view["agent"]
    wiring = FLY_MODELS.get(a.get("model")) or FLY_MODELS.get(a.get("persona"))
    if wiring is None:
        from sim.policy import decide as rule_decide
        return rule_decide(view)
    return fly_decide(view, wiring)


def fly_decide(view: dict, wiring: str = "real") -> dict:
    t0 = time.perf_counter()
    geo = Geometry(view)
    cell, evidence = stimulus(view, geo)
    k = key(cell)
    tab = table()
    rates, base = tab["entries"][k][wiring], tab["baseline"][wiring]
    motor = motor_command(rates, base)
    prim = primitive(motor)
    desired = desired_bearing(prim, motor, cell)
    opt, bearings = pick(view["options"], geo, prim, desired)
    why = f"{prim.replace('_', ' ')} (connectome table {k}, {wiring} wiring)"
    rec = {
        "decision": {"padm": {
            "threat_real": "yes" if evidence else "no", "threat_evidence": evidence,
            "need_action": "no" if prim == "freeze" else "yes", "need_evidence": evidence,
            "options_considered": [o["id"] for o in view["options"]],
            "choice": opt["id"], "why": why, "choice_evidence": evidence, "when": "now"}},
        "validation": {"status": "ok", "errors": []},
        "raw_output": None, "cost_usd": 0.0,
        "fly": {"wiring": wiring, "table_key": k, "stimulus": {c: list(v) for c, v in cell.items()},
                "dn_rates_hz": rates, "dn_baseline_hz": base, "motor": motor, "primitive": prim,
                "desired_bearing_deg": desired, "option_bearings_deg": bearings,
                "heading_from": geo.heading_from,
                "provenance": {k2: tab["provenance"].get(k2) for k2 in
                               ("built_at", "connectome", "model", "flyscore_commit", "scrambled", "window_ms",
                                "trials")}},
    }
    rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return {"choice": opt["id"], "say": None, "record": rec}


# ---- geometry -------------------------------------------------------------------------------
class Geometry:
    """Egocentric bearings for one view; positions from scene.json when no bearing is given."""

    def __init__(self, view: dict) -> None:
        a = view["agent"]
        sc = scene_points()
        self.exits, self.rooms = sc
        self.pos = a.get("pos")
        self.heading_from, self.heading = None, None
        for ref in (a.get("target_exit"), a.get("entry_exit"), *a.get("known_exits", [])):
            if ref in self.exits and self.pos is not None:
                v = (self.exits[ref][0] - self.pos[0], self.exits[ref][1] - self.pos[1])
                if math.hypot(*v) > 1e-6:
                    self.heading_from, self.heading = ref, v
                    break

    def bearing(self, item: dict, point: list | None) -> float | None:
        if item.get("bearing_deg") is not None:
            return float(item["bearing_deg"])
        if point is None or self.heading is None:
            return None
        hx, hz = self.heading
        vx, vz = point[0] - self.pos[0], point[1] - self.pos[1]
        # Y up, right-handed: (h x v)_y > 0 means v is on the left
        return round(math.degrees(math.atan2(hz * vx - hx * vz, hx * vx + hz * vz)), 1)


def side_of(bearing: float | None) -> str:
    if bearing is None or abs(bearing) < SIDE_DEG or abs(bearing) > 180 - SIDE_DEG:
        return "B"
    return "L" if bearing > 0 else "R"


@lru_cache(maxsize=4)
def _load_scene(path: str) -> tuple[dict, dict]:
    s = json.loads(Path(path).read_text())
    exits = {e["id"]: e["position"] for e in s.get("exits", []) if e.get("position")}
    rooms = {r["id"]: r["centroid"] for r in s.get("rooms", []) if r.get("centroid")}
    return exits, rooms


def scene_points() -> tuple[dict, dict]:
    """(exit id -> [x, z], room id -> centroid) from FLYAGENT_SCENE or the sim's --scene/--scenario."""
    path = os.environ.get("FLYAGENT_SCENE") or _scene_from_argv()
    if not path or not Path(path).exists():
        return {}, {}
    return _load_scene(str(Path(path).resolve()))


def _scene_from_argv() -> str | None:
    # ponytail: sniffs `python -m sim run --scene/--scenario` until the view carries bearings
    argv = sys.argv
    val = lambda flag: argv[argv.index(flag) + 1] if flag in argv[:-1] else None
    if val("--scene"):
        return val("--scene")
    scen = val("--scenario")
    if not scen or not Path(scen).exists():
        return None
    raw = json.loads(Path(scen).read_text()).get("scene")
    if not raw:
        return None
    p = Path(raw)
    return str(p if p.exists() or p.is_absolute() else Path(scen).parent / raw)


# ---- percepts -> stimulus cell ----------------------------------------------------------------
def _merge(cell: dict, ch: str, side: str, level: int) -> None:
    old_side, old_level = cell.get(ch, ("-", 0))
    if old_level == 0:
        cell[ch] = (side, level)
    else:
        cell[ch] = (old_side if old_side == side else "B", max(level, old_level))


def stimulus(view: dict, geo: Geometry) -> tuple[dict, list[str]]:
    """Grid cell {channel: (side, level)} and the percept ids that produced it."""
    cell: dict = {}
    used: list[str] = []
    signs = []
    for p in view.get("percepts", []):
        kind = p.get("kind")
        if kind in ("fire", "smoke"):
            near = kind == "fire" or (p.get("dist_m") or 99) < NEAR_SMOKE_M
            _merge(cell, "smoke", side_of(geo.bearing(p, geo.rooms.get(p.get("where")))), 2 if near else 1)
        elif kind == "people_moving" and p.get("count", 0) >= LOOM_MIN_PEOPLE:
            _merge(cell, "loom", side_of(geo.bearing(p, None)), 1)
        elif kind == "alarm" or (kind == "message" and p.get("urgent")):
            cell["sound"] = ("B", 1)
        elif kind == "exit_sign":
            signs.append(p)
        else:
            continue
        used.append(p["id"])
    if signs:
        s = min(signs, key=lambda p: p.get("dist_m") or 99)
        b = geo.bearing(s, geo.exits.get(s.get("ref")))
        if b is not None:  # light has no "both" level: a sign dead ahead/behind goes to the side it leans to
            cell["light"] = ("L" if b >= 0 else "R", 1)
            used.append(s["id"])
    return cell, used


# ---- DN rates -> motor primitive -> option ---------------------------------------------------
def motor_command(r: dict, base: dict) -> dict:
    """fly4 drunkfly/loop.py transduce(): DN rates relative to this wiring's no-stimulus rates."""
    diff = lambda n: (r[f"{n}_L"] - r[f"{n}_R"]) - (base[f"{n}_L"] - base[f"{n}_R"])
    fwd = lambda x: sum(x[f"P9_{s}"] + 0.5 * x[f"oDN1_{s}"] for s in "LR")
    return {
        "turn_hz": round(diff("DNa01") + 0.5 * diff("DNa02"), 2),  # + = turn left (Rayshubskiy 2020)
        "forward_ratio": round(fwd(r) / max(fwd(base), 1.0), 3),
        "gf_hz": round((r["GF_L"] + r["GF_R"]) / 2, 2),
        "mdn_hz": round((r["MDN_L"] + r["MDN_R"]) / 2, 2),
    }


def primitive(m: dict) -> str:
    if m["gf_hz"] >= GF_ESCAPE_HZ:
        return "escape"
    if m["mdn_hz"] >= MDN_BACK_HZ:
        return "backward"
    if m["forward_ratio"] < FREEZE_RATIO and abs(m["turn_hz"]) < TURN_MIN_HZ:
        return "freeze"
    if abs(m["turn_hz"]) >= TURN_MIN_HZ:
        return "turn_left" if m["turn_hz"] > 0 else "turn_right"
    return "forward"


def desired_bearing(prim: str, m: dict, cell: dict) -> float | None:
    if prim == "forward":
        return 0.0
    if prim.startswith("turn"):
        return round(math.copysign(min(1.0, max(1 / 3, abs(m["turn_hz"]) / TURN_FULL_HZ)) * 90, m["turn_hz"]), 1)
    if prim in ("escape", "backward"):
        threat = {cell[c][0] for c in ("loom", "smoke") if cell.get(c, ("-", 0))[1]}
        if threat == {"L"}:
            return -135.0
        if threat == {"R"}:
            return 135.0
        return 180.0
    return None  # freeze


def _ang(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def pick(options: list[dict], geo: Geometry, prim: str, desired: float | None) -> tuple[dict, dict]:
    by_action = {o["action"]: o for o in reversed(options)}
    bearings = {o["id"]: geo.bearing(o, geo.exits.get(o.get("exit"))) for o in options if o.get("exit")}
    if prim == "backward" and "TURN_BACK" in by_action:
        return by_action["TURN_BACK"], bearings
    if prim == "freeze" or desired is None:
        stay = next((by_action[a] for a in STAY_ACTIONS if a in by_action), None)
        if stay:
            return stay, bearings
        desired = 0.0
    steer = [o for o in options if bearings.get(o["id"]) is not None]
    if steer:
        return min(steer, key=lambda o: (_ang(bearings[o["id"]], desired), o.get("route_m", 0.0))), bearings
    moving = [o for o in options if o.get("exit")]
    if moving:  # no geometry: any motor output means leaving by the shortest route
        return min(moving, key=lambda o: o.get("route_m", 0.0)), bearings
    return options[0], bearings
