"""Interventions as scenario/scene patches (docs/SIM_INTERFACES.md "Interventions") and their validation.

An intervention is {"id", "kind", "args", "why"}; apply() turns a list of them into a new
(scene, scenario) pair without mutating the inputs. Every patch is checked twice: the
intervention's own limits here, then the patched scenario (scenario.schema.validate plus
the sim extensions below) and the patched scene (contract.validate_scene, no new errors).
"""

from __future__ import annotations

import copy

from contract.validate_scene import validate as validate_scene
from scenario import schema

KINDS = ("add_warden", "add_signage", "pa_announcement", "unlock_exit", "assign_familiar_exit", "widen_door")
MAX_WARDENS = 2  # per intervention
MAX_DOOR_M = 2.4  # contract allows <= 3.0; 2.4 m is a double leaf door
MAX_WIDEN_FACTOR = 1.5
UNLOCK_DELAY_S = 30.0  # a key holder reaches a locked exit after the alarm
EXT_EVENTS = ("pa_announcement", "open_exit")  # sim event types the schema may not list yet
SIGNAGE_P = 0.8


def _ids(scene: dict) -> dict:
    return {"room": {r["id"] for r in scene["rooms"] if r["type"] != "outside"},
            "exit": {e["id"] for e in scene["exits"]}, "door": {d["id"] for d in scene["doors"]}}


def validate_scenario(sc: dict, scene: dict) -> list[str]:
    """scenario.schema.validate, after checking and removing the sim extensions it does not know yet
    (pa_announcement / open_exit events, occupants[].familiar_exit, top-level interventions)."""
    ids, errs = _ids(scene), []
    rest = copy.deepcopy(sc)
    keep = []
    for i, ev in enumerate(rest.get("events", [])):
        if ev.get("type") not in EXT_EVENTS or ev.get("type") in schema.EVENT_TYPES:
            keep.append(ev)
            continue
        at, kind = ev.get("at") or {}, "exit" if ev["type"] == "open_exit" else "room"
        if at.get(kind) not in ids[kind]:
            errs.append(f"events[{i}].at.{kind}: unknown {kind} {at.get(kind)!r}")
        if not 0 <= float(ev.get("t_start_s", -1)) <= float(sc.get("duration_s", 300)):
            errs.append(f"events[{i}].t_start_s: must be within [0, duration_s]")
        if ev["type"] == "pa_announcement" and not str((ev.get("params") or {}).get("text", "")).strip():
            errs.append(f"events[{i}].params.text: the announcement needs its text")
        claim = (ev.get("params") or {}).get("claim") or {}
        if claim.get("exit_open") and claim["exit_open"] not in ids["exit"]:
            errs.append(f"events[{i}].params.claim.exit_open: unknown exit {claim['exit_open']!r}")
    rest["events"] = keep
    for i, g in enumerate(rest.get("occupants", [])):
        fam = g.pop("familiar_exit", None)
        if fam is not None and fam not in ids["exit"]:
            errs.append(f"occupants[{i}].familiar_exit: unknown exit {fam!r}")
    for i, it in enumerate(rest.pop("interventions", [])):
        if it.get("type") != "signage" or it.get("exit") not in ids["exit"]:
            errs.append(f"interventions[{i}]: expected {{type: signage, exit: <exit id>}}, got {it}")
    return errs + schema.validate(rest, scene)


def _blocked_exits(sc: dict) -> dict[str, dict]:
    return {e["at"]["exit"]: e for e in sc.get("events", []) if e["type"] == "blocked_exit" and "exit" in e["at"]}


def check(iv: dict, scene: dict, sc: dict) -> list[str]:
    """Limits of one intervention against the current scene and scenario; [] when allowed."""
    ids, kind, a = _ids(scene), iv.get("kind"), iv.get("args") or {}
    if kind not in KINDS:
        return [f"kind: {kind!r} is not on the menu {KINDS}"]
    errs = []
    for key, pool in (("room", "room"), ("exit", "exit"), ("door", "door")):
        if key in a and a[key] not in ids[pool]:
            errs.append(f"args.{key}: unknown {pool} {a[key]!r}; known: {sorted(ids[pool])[:12]}")
    if kind == "add_warden" and not 1 <= int(a.get("count", 1)) <= MAX_WARDENS:
        errs.append(f"args.count: 1..{MAX_WARDENS} wardens per intervention")
    if kind in ("add_warden", "add_signage", "assign_familiar_exit") and "room" not in a:
        errs.append("args.room: required")
    if kind in ("add_signage", "unlock_exit", "assign_familiar_exit") and "exit" not in a:
        errs.append("args.exit: required")
    if kind == "unlock_exit" and a.get("exit") in ids["exit"]:
        ev = _blocked_exits(sc).get(a["exit"])
        opened = any(e["type"] == "open_exit" and e["at"].get("exit") == a["exit"] for e in sc.get("events", []))
        if ev is None:
            errs.append(f"args.exit: {a['exit']} is not blocked")
        elif opened:
            errs.append(f"args.exit: {a['exit']} already has a key holder assigned (open_exit event)")
        elif (ev.get("params") or {}).get("cause", "locked") not in ("locked", "chained", "obstructed"):
            errs.append(f"args.exit: {a['exit']} is blocked by {ev['params'].get('cause')}, staff cannot clear it")
    if kind == "pa_announcement":
        if not str(a.get("text", "")).strip():
            errs.append("args.text: required")
        if a.get("exit_open") and a["exit_open"] not in ids["exit"]:
            errs.append(f"args.exit_open: unknown exit {a['exit_open']!r}")
        if not 0 <= float(a.get("t_s", -1)) <= float(sc.get("duration_s", 300)):
            errs.append("args.t_s: must be within the run")
    if kind == "widen_door" and a.get("door") in ids["door"]:
        d = next(d for d in scene["doors"] if d["id"] == a["door"])
        w, cap = float(a.get("width_m", 0)), min(MAX_DOOR_M, round(d["width_m"] * MAX_WIDEN_FACTOR, 2))
        if not d["width_m"] < w <= cap:
            errs.append(f"args.width_m: {a['door']} is {d['width_m']} m; allowed ({d['width_m']}, {cap}] m")
    return errs


def apply_one(iv: dict, scene: dict, sc: dict) -> tuple[dict, dict]:
    """New (scene, scenario) with one checked intervention applied."""
    scene, sc = copy.deepcopy(scene), copy.deepcopy(sc)
    a, kind, n = iv["args"], iv["kind"], iv["id"]
    if kind == "add_warden":
        sc["occupants"].append({"where": {"room": a["room"]}, "count": int(a.get("count", 1)),
                                "personas": {"staff": 1.0}, "models": _models(sc)})
    elif kind == "add_signage":
        # ponytail: the sim applies signage building-wide (each occupant learns the exit with p 0.8);
        # args.room is where the sign goes, kept for the report. Per-room signs need a sim hook.
        sc.setdefault("interventions", []).append({"type": "signage", "exit": a["exit"], "p": SIGNAGE_P})
    elif kind == "assign_familiar_exit":
        for g in sc["occupants"]:
            if g["where"].get("room") == a["room"]:
                g["familiar_exit"] = a["exit"]
    elif kind == "pa_announcement":
        sc["events"].append({"id": n, "type": "pa_announcement", "t_start_s": float(a["t_s"]),
                             "at": {"room": a.get("room") or _any_room(scene)},
                             "params": {"text": a["text"], "claim": {"exit_open": a["exit_open"]} if a.get("exit_open") else {}}})
    elif kind == "unlock_exit":
        t_block = float(_blocked_exits(sc)[a["exit"]]["t_start_s"])
        sc["events"].append({"id": n, "type": "open_exit", "t_start_s": t_block + UNLOCK_DELAY_S,
                             "at": {"exit": a["exit"]}, "params": {}})
    elif kind == "widen_door":
        for d in scene["doors"]:
            if d["id"] == a["door"]:
                d["width_m"] = float(a["width_m"])
        for e in scene["exits"]:
            if e["door_id"] == a["door"]:
                e["width_m"] = float(a["width_m"])
    return scene, sc


def _models(sc: dict) -> dict:
    return next((g["models"] for g in sc.get("occupants", []) if g.get("models")), {schema.DEFAULT_MODEL: 1.0})


def _any_room(scene: dict) -> str:
    return next(r["id"] for r in scene["rooms"] if r["type"] != "outside")


def _scene_errors(scene: dict) -> set[str]:
    return {f"{i['code']}:{i.get('ref')}" for i in validate_scene(scene) if i["severity"] == "error"}


def try_apply(iv: dict, scene: dict, sc: dict) -> tuple[dict, dict, list[str]]:
    """(scene, scenario, errors). On any error the inputs come back unchanged."""
    errs = check(iv, scene, sc)
    if errs:
        return scene, sc, errs
    new_scene, new_sc = apply_one(iv, scene, sc)
    errs = validate_scenario(new_sc, new_scene)
    if new_scene != scene:
        errs += sorted(_scene_errors(new_scene) - _scene_errors(scene))
    return (scene, sc, errs) if errs else (new_scene, new_sc, [])
