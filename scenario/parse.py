"""Click and type (docs/LIVE_MODE.md section 6): free text typed at a clicked spot -> validated edits.

parse_event(scene, "fire spreading from here", {"point": [x, z], "level": "L1"}, t_s=42)
returns {"events", "occupants", "questions", "assumptions", "cost_usd"}. Code works out what
the click is on (room, nearest door, exit, stair) and hands it to the same tool loop as
scenario/chat.py, so "here" and "this door" become ids without the model guessing. An
ambiguous place stops the loop and comes back as a question (no events) for the client
to answer and resend.
"""

from __future__ import annotations

import hashlib
import json
from typing import Callable

from shapely.geometry import LineString, Point, Polygon

from .chat import TOOLS, NeedUser, _system, raise_need_user, run
from .schema import EVENT_TYPES

NEAR_DOOR_M = 1.5  # a click this close to a door (or exit, stair) counts as "this door"
RUN_TAIL_S = 300  # validation horizon after the edit time

SUBMIT_EDIT = {"type": "function", "function": {
    "name": "submit_edit",
    "description": "Submit the events and/or occupants this text adds. Returns ok or validation errors to fix.",
    "parameters": {"type": "object", "required": ["events", "occupants", "assumptions"], "properties": {
        "events": TOOLS[2]["function"]["parameters"]["properties"]["events"],
        "occupants": TOOLS[2]["function"]["parameters"]["properties"]["occupants"],
        "assumptions": {"type": "array", "items": {"type": "string"}}}}}}
EDIT_TOOLS = [TOOLS[0], TOOLS[1], SUBMIT_EDIT]


def click_context(scene: dict, at: dict | None) -> dict:
    """What a click lands on, by geometry: containing room and the nearest door, exit and stair."""
    if not at or "point" not in at:
        return {}
    lv, pt = at.get("level") or scene["levels"][0]["id"], Point(at["point"])
    ctx: dict = {"point": [round(v, 2) for v in at["point"]], "level": lv}
    room = next((r for r in scene["rooms"] if r["level"] == lv and r["type"] != "outside"
                 and Polygon(r["polygon"]).covers(pt)), None)
    if room:
        ctx["room"] = {"id": room["id"], "name": room["name"], "type": room["type"]}
    near = [("door", d["id"], LineString(d["segment"]).distance(pt)) for d in scene["doors"] if d["level"] == lv]
    near += [("exit", e["id"], Point(e["position"]).distance(pt)) for e in scene["exits"] if e["level"] == lv]
    near += [("stair", s["id"], Polygon(s["polygon"]).distance(pt)) for s in scene.get("stairs", [])
             if lv in (s["from_level"], s["to_level"])]
    for kind in ("door", "exit", "stair"):
        best = min((n for n in near if n[0] == kind), key=lambda n: n[2], default=None)
        if best and best[2] <= NEAR_DOOR_M:
            ctx[f"nearest_{kind}"] = {"id": best[1], "dist_m": round(best[2], 2)}
    return ctx


def _system_edit(scene: dict, ctx: dict, t_s: float) -> str:
    base = _system(scene).split("BUILDING", 1)[1]
    where = json.dumps(ctx) if ctx else "none (no click; use resolve_location for every place)"
    return f"""The user clicked a spot on the floor plan of a running evacuation simulation and typed a short
instruction. Turn it into scenario events and/or occupants and call submit_edit. Rules:
- "here", "this room", "this door", "this exit", "this stair" mean the clicked location below. Use its
  ids: room for fire/smoke/rumor/pa_announcement and for people, nearest_door for a door,
  nearest_exit for an exit, nearest_stair for a stair. Use {{"point": [x, z], "level": id}} only for an
  event when the click is in no room.
- Other places: call resolve_location; never invent ids. If it is ambiguous, call ask_user with the
  candidates.
- Event types: {", ".join(EVENT_TYPES)}. Blocking a door, exit or stair = blocked_exit with at
  {{"exit": id}}, {{"door": id}} or {{"stair": id}} (prefer exit when the door is an exit).
  rumor: params.text and params.claim {{"exit_blocked": id}}; pa_announcement: params.text, optional
  params.claim {{"exit_open": id}}; open_exit: at {{"exit": id}}.
- t_start_s is {t_s:g} (now) unless the text gives a delay ("in 30 s" = {t_s + 30:g}).
- People: occupants with where {{"room": id}}, count, personas (weights sum to 1) from: warden,
  regular_employee, first_time_visitor, committed_task, parent_with_child, older_adult,
  mobility_impaired, skeptic. "mostly older adults" = about 0.7 older_adult.
- Write every guess into assumptions. Submit only what the text asks for.
CLICKED LOCATION: {where}
BUILDING{base}"""


def _ids(text: str, at: dict | None, t_s: float) -> str:
    return "u" + hashlib.sha1(f"{text}|{json.dumps(at, sort_keys=True)}|{t_s}".encode()).hexdigest()[:6]


def parse_event(scene: dict, text: str, at: dict | None = None, t_s: float = 0.0, scene_path: str = "",
                llm: Callable | None = None, history: list[dict] | None = None) -> dict:
    """Validated events/occupants for one typed instruction; questions instead when a place is ambiguous."""
    ctx = click_context(scene, at)
    out = {"events": [], "occupants": [], "questions": [], "assumptions": [], "cost_usd": 0.0, "click": ctx}
    overrides = {"duration_s": float(t_s) + RUN_TAIL_S}
    try:
        scenario, cost, _ = run(scene, scene_path, text, raise_need_user, llm=llm, system=_system_edit(scene, ctx, t_s),
                                tools=EDIT_TOOLS, history=history, overrides=overrides)
    except NeedUser as q:
        return {**out, "questions": [{"question": q.question, "options": q.options}], "cost_usd": round(q.cost, 6)}
    out["cost_usd"] = round(cost, 6)
    if scenario is None:
        return {**out, "questions": [{"question": "I could not turn that into a valid edit. Can you rephrase it?",
                                      "options": []}]}
    prefix = _ids(text, at, t_s)
    events = [{**e, "id": f"{prefix}_{i}"} for i, e in enumerate(scenario["events"])]
    return {**out, "events": events, "occupants": scenario["occupants"], "assumptions": scenario["assumptions"]}
