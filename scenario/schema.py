"""Pydantic model of scenario.json (docs/SIM_INTERFACES.md) and validation against a scene.

validate() returns a list of plain error strings ("events[0].at.room: unknown room
'r_9'; known: ...") so an LLM can repair its own submission.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

import shapely
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from shapely.geometry import Point, Polygon

EVENT_TYPES = ("fire", "smoke", "alarm_only", "active_intruder", "blocked_exit", "rumor")
# Archetypes from research/EVAC_PSYCHOLOGY.md, then the sim's short names
# (sim/personas.py: staff, regular, visitor, committed, parent); sim and cognition alias both.
PERSONAS = ("warden", "regular_employee", "first_time_visitor", "committed_task", "parent_with_child",
            "older_adult", "mobility_impaired", "skeptic", "regular", "visitor", "staff", "committed", "parent")
DEFAULT_PERSONAS = {"regular_employee": 0.6, "first_time_visitor": 0.25, "warden": 0.1, "mobility_impaired": 0.05}
DEFAULT_MODEL = "google/gemini-2.5-flash-lite"
MAX_PER_M2 = 2.0  # hard cap per room [H], research/SCENARIOS_AND_MODELS.md 2.2
WEIGHT_TOL = 0.01

Point2 = Annotated[list[float], Field(min_length=2, max_length=2)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class At(_Model):
    room: Optional[str] = None
    exit: Optional[str] = None
    door: Optional[str] = None
    point: Optional[Point2] = None
    level: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one(self):
        set_ = [k for k in ("room", "exit", "door", "point") if getattr(self, k) is not None]
        if len(set_) != 1:
            raise ValueError(f"exactly one of room, exit, door, point; got {set_ or 'none'}")
        if (self.point is None) != (self.level is None):
            raise ValueError("point and level go together")
        return self


class Event(_Model):
    id: str
    type: Literal[EVENT_TYPES]
    t_start_s: float = Field(ge=0)
    at: At
    params: dict[str, Any] = {}


class Where(_Model):
    room: str


class Occupants(_Model):
    where: Where
    count: int = Field(ge=1, le=2000)
    personas: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_PERSONAS))
    models: dict[str, float] = Field(default_factory=lambda: {DEFAULT_MODEL: 1.0})


class Scenario(_Model):
    schema_version: Literal["0.1"] = "0.1"
    scene: str
    seed: int = 42
    duration_s: float = Field(default=300, gt=0, le=3600)
    events: list[Event] = []
    occupants: list[Occupants] = []
    assumptions: list[str] = []


def _known(items: dict, n: int = 12) -> str:
    keys = list(items)[:n]
    return ", ".join(f"{k} ({items[k]})" if items[k] != k else k for k in keys) + (" ..." if len(items) > n else "")


def _inside_walkable(scene: dict, level: str, pt: list[float]) -> bool:
    polys = [Polygon(p["exterior"], p.get("holes", [])) for p in scene["walkable"].get(level, {}).get("polygons", [])]
    return bool(polys) and shapely.union_all(polys).covers(Point(pt))


def _check_at(at: At, where: str, scene: dict, ids: dict) -> list[str]:
    for kind in ("room", "exit", "door"):
        ref = getattr(at, kind)
        if ref is not None and ref not in ids[kind]:
            return [f"{where}.{kind}: unknown {kind} '{ref}'; known: {_known(ids[kind])}"]
    if at.point is not None:
        if at.level not in ids["level"]:
            return [f"{where}.level: unknown level '{at.level}'; known: {_known(ids['level'])}"]
        if not _inside_walkable(scene, at.level, at.point):
            return [f"{where}.point: {at.point} is not inside walkable area of level {at.level}"]
    return []


def _check_event(i: int, ev: Event, sc: Scenario, scene: dict, ids: dict, rooms: dict) -> list[str]:
    w = f"events[{i}]"
    errs = _check_at(ev.at, f"{w}.at", scene, ids)
    if ev.t_start_s > sc.duration_s:
        errs.append(f"{w}.t_start_s: {ev.t_start_s} is after duration_s {sc.duration_s}")
    if ev.type == "blocked_exit" and ev.at.exit is None:
        errs.append(f"{w}.at: blocked_exit needs {{\"exit\": id}}; exits: {_known(ids['exit'])}")
    if ev.type in ("fire", "smoke") and ev.at.room in rooms and rooms[ev.at.room]["type"] == "outside":
        errs.append(f"{w}.at.room: {ev.type} cannot start in an outside room")
    if ev.type == "rumor":
        if not isinstance(ev.params.get("text"), str) or not ev.params["text"].strip():
            errs.append(f"{w}.params.text: rumor needs the message text")
        claim = ev.params.get("claim", {})
        if not isinstance(claim, dict):
            errs.append(f"{w}.params.claim: must be an object such as {{\"exit_blocked\": \"<exit id>\"}}")
        elif "exit_blocked" in claim and claim["exit_blocked"] not in ids["exit"]:
            errs.append(f"{w}.params.claim.exit_blocked: unknown exit '{claim['exit_blocked']}'; "
                        f"known: {_known(ids['exit'])}")
    return errs


def _check_weights(where: str, weights: dict[str, float], allowed: tuple[str, ...] | None) -> list[str]:
    errs = [f"{where}: unknown persona '{k}'; known: {', '.join(allowed)}" for k in weights if allowed and k not in allowed]
    if any(v < 0 for v in weights.values()):
        errs.append(f"{where}: weights must be >= 0")
    if abs(sum(weights.values()) - 1) > WEIGHT_TOL:
        errs.append(f"{where}: weights sum to {sum(weights.values()):.3f}, must sum to 1")
    return errs


def validate(data: dict, scene: dict) -> list[str]:
    """All problems with a scenario dict against a scene dict; [] when valid."""
    try:
        sc = Scenario.model_validate(data)
    except ValidationError as e:
        return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
    rooms = {r["id"]: r for r in scene["rooms"]}
    ids = {"room": {k: r["name"] for k, r in rooms.items()},
           "exit": {e["id"]: e["label"] for e in scene["exits"]},
           "door": {d["id"]: d["id"] for d in scene["doors"]},
           "level": {lv["id"]: lv["name"] for lv in scene["levels"]}}
    errs = []
    seen = set()
    for i, ev in enumerate(sc.events):
        if ev.id in seen:
            errs.append(f"events[{i}].id: duplicate id '{ev.id}'")
        seen.add(ev.id)
        errs += _check_event(i, ev, sc, scene, ids, rooms)
    per_room: dict[str, int] = {}
    for i, occ in enumerate(sc.occupants):
        w = f"occupants[{i}]"
        rid = occ.where.room
        if rid not in rooms:
            errs.append(f"{w}.where.room: unknown room '{rid}'; known: {_known(ids['room'])}")
            continue
        if rooms[rid]["type"] == "outside":
            errs.append(f"{w}.where.room: '{rid}' is outside the building")
        per_room[rid] = per_room.get(rid, 0) + occ.count
        errs += _check_weights(f"{w}.personas", occ.personas, PERSONAS)
        errs += _check_weights(f"{w}.models", occ.models, None)
    for rid, n in per_room.items():
        cap = int(MAX_PER_M2 * rooms[rid]["area_m2"])
        if n > cap:
            errs.append(f"occupants: room {rid} ({rooms[rid]['name']}, {rooms[rid]['area_m2']:.0f} m2) holds at most "
                        f"{cap} people ({MAX_PER_M2:g} per m2), got {n}")
    return errs


def normalize(data: dict) -> dict:
    """Valid scenario dict with defaults filled in (personas, models, params), unset `at` keys dropped."""
    return Scenario.model_validate(data).model_dump(exclude_none=True)
