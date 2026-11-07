"""Static scene plus mutable hazard state: exits, doors with flow buckets, fields, events.

Units: meters, seconds, persons/s. Door flow follows research/EVAC_PSYCHOLOGY.md
(1.3 persons/s per meter of door width).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from sim.navmesh import INF, NavMesh

log = logging.getLogger("sim")

FLOW_P_S_M = 1.3
FIRE_COST, SMOKE_COST = 10.0, 4.0
DEFAULT_BLOCK_AFTER_S = 60.0
DEFAULT_RUMOR_RADIUS_M = 8.0


@dataclass
class Bucket:
    """Token bucket: at most 1 + cap*dt burst, cap persons/s long run."""

    cap: float
    dt: float
    tokens: float = 1.0

    def refill(self) -> None:
        self.tokens = min(self.tokens + self.cap * self.dt, 1.0 + self.cap * self.dt)

    def take(self) -> bool:
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


@dataclass
class ExitState:
    id: str
    label: str
    pos: np.ndarray
    goal: np.ndarray  # nearest walkable point; agents leave within EXIT_RADIUS_M of it
    seed_tri: int
    width_m: float
    bucket: Bucket
    door_id: str = ""
    blocked: bool = False


@dataclass
class DoorState:
    id: str
    a: np.ndarray
    b: np.ndarray
    width_m: float
    tris: np.ndarray
    connects: list[str]
    bucket: Bucket


@dataclass
class Event:
    id: str
    type: str
    t_start: float
    params: dict
    room: str | None = None
    exit: str | None = None
    door: str | None = None
    point: np.ndarray | None = None
    phase: int = 0  # fire/smoke: 0 not started, 1 costly, 2 impassable; others: 1 fired
    tris: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))


class World:
    """Scene geometry, exits, doors, hazard costs and per-exit distance fields."""

    def __init__(self, scene: dict, dt: float) -> None:
        levels = [lv["id"] for lv in scene["levels"]]
        level = next((e["level"] for e in scene["exits"]), levels[0])
        if len(levels) > 1:
            log.warning("multi-level scene: simulating level %s only (stairs not modeled)", level)
        self.nav = NavMesh(scene, level)
        self.dt = dt
        self.rooms = {r["id"]: r for r in scene["rooms"] if r["level"] == level}
        self.room_polys = {rid: Polygon(r["polygon"]).buffer(0) for rid, r in self.rooms.items()}
        self.door_by_id = {d["id"]: d for d in scene["doors"]}
        self.exits = self._build_exits(scene, level)
        self.doors = self._build_doors(scene, level)
        self.fields: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.refresh_fields()

    def _build_exits(self, scene: dict, level: str) -> dict[str, ExitState]:
        out = {}
        for e in scene["exits"]:
            if e["level"] != level:
                continue
            goal = self.nav.snap(e["position"])
            w = float(e.get("width_m") or 0.9)
            out[e["id"]] = ExitState(e["id"], e.get("label") or e["id"], np.asarray(e["position"], float), goal,
                                     int(self.nav.locate(goal[None])[0]), w, Bucket(FLOW_P_S_M * w, self.dt),
                                     e["door_id"])
        if not out:
            raise ValueError("scene has no exits on the simulated level")
        return out

    def _build_doors(self, scene: dict, level: str) -> list[DoorState]:
        exit_doors = {e["door_id"] for e in scene["exits"]}
        out = []
        for d in scene["doors"]:
            if d["level"] != level or d["id"] in exit_doors:
                continue
            seg = np.asarray(d["segment"], float)
            w = float(d.get("width_m") or np.linalg.norm(seg[1] - seg[0]))
            out.append(DoorState(d["id"], seg[0], seg[1], w, self.nav.tris_in(LineString(seg)), d["connects"],
                                 Bucket(FLOW_P_S_M * w, self.dt)))
        return out

    # ---- routing ---------------------------------------------------------
    def refresh_fields(self) -> None:
        """Recompute every exit's cost-to-go field from the current triangle multipliers."""
        self.fields = {eid: self.nav.distance_field(ex.seed_tri) for eid, ex in self.exits.items()}

    def route_m(self, exit_id: str, tri: int) -> float:
        """Cost-weighted route length in m from triangle `tri` to the exit (inf if cut off)."""
        return float(self.fields[exit_id][0][tri])

    def path_to_exit(self, pos: np.ndarray, tri: int, exit_id: str) -> list[np.ndarray] | None:
        """Funnel waypoints from pos to the exit goal, or None when unreachable."""
        dist, nxt = self.fields[exit_id]
        if not math.isfinite(dist[tri]):
            return None
        corr = self.nav.corridor(tri, nxt)
        return self.nav.funnel(pos, self.exits[exit_id].goal, corr)[1:]

    def room_at(self, pt: np.ndarray) -> str | None:
        p = Point(pt)
        return next((rid for rid, poly in self.room_polys.items() if poly.covers(p)), None)

    def room_point(self, room: str, rng: np.random.Generator, tries: int = 200) -> np.ndarray:
        """Uniform random walkable point inside a room (falls back to its snapped centroid)."""
        poly = self.room_polys[room]
        x0, z0, x1, z1 = poly.bounds
        for _ in range(tries):
            p = rng.uniform([x0, z0], [x1, z1])
            if poly.contains(Point(p)) and self.nav.walk.contains(Point(p)):
                return p
        return self.nav.snap(poly.representative_point().coords[0])

    # ---- events ----------------------------------------------------------
    def resolve_event(self, ev: dict) -> Event:
        """Turn a scenario event into an Event with its location resolved to ids and a point."""
        at = ev.get("at") or {}
        out = Event(ev.get("id") or f"ev_{id(ev)}", ev["type"], float(ev.get("t_start_s", 0.0)),
                    dict(ev.get("params") or {}))
        if "room" in at:
            out.room = at["room"]
            if out.room in self.room_polys:
                poly = self.room_polys[out.room].intersection(self.nav.walk)
                src = poly if not poly.is_empty else self.room_polys[out.room]
                out.point = self.nav.snap(src.representative_point().coords[0])
            else:
                log.warning("event %s: unknown room %s", out.id, out.room)
        elif "exit" in at:
            out.exit = at["exit"]
            if out.exit in self.exits:
                out.point = self.exits[out.exit].goal
        elif "door" in at:
            out.door = at["door"]
            d = self.door_by_id.get(out.door)
            ex = next((e for e in self.exits.values() if e.door_id == out.door), None)
            if ex:
                out.exit = ex.id
            if d:
                out.point = self.nav.snap(np.mean(d["segment"], axis=0))
        elif "point" in at:
            out.point = self.nav.snap(at["point"])
            out.room = self.room_at(out.point)
        if out.type in ("fire", "smoke"):
            out.tris = self._hazard_tris(out)
        return out

    def _hazard_tris(self, ev: Event) -> np.ndarray:
        if ev.room in self.room_polys:
            parts = [self.nav.tris_with_centroid_in(self.room_polys[ev.room])]
            parts += [d.tris for d in self.doors if ev.room in d.connects]
            return np.unique(np.concatenate(parts)).astype(int)
        if ev.point is not None:
            return self.nav.tris_in(Point(ev.point).buffer(3.0))
        return np.zeros(0, dtype=int)

    def set_cost(self, tris: np.ndarray, mult: float) -> None:
        """Raise the entry cost of triangles (never lowers it) and refresh fields."""
        if len(tris):
            self.nav.mult[tris] = np.maximum(self.nav.mult[tris], mult)
            self.refresh_fields()

    def block_door(self, door_id: str) -> None:
        d = next((d for d in self.doors if d.id == door_id), None)
        if d is not None:
            self.set_cost(d.tris, INF)
