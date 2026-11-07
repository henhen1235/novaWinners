"""Static scene plus mutable hazard state: exits, doors, stairs, flow buckets, fields, events.

Units: meters, seconds, persons/s. Door flow 1.3 persons/s per meter of width,
stair flow 1.1 persons/s per meter (research/EVAC_PSYCHOLOGY.md, SFPE). Levels
are indices into NavMesh.level_ids.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from sim.navmesh import INF, Link, NavMesh, Waypoint

log = logging.getLogger("sim")

FLOW_P_S_M = 1.3
STAIR_FLOW_P_S_M = 1.1
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
    lv: int = 0
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
    lv: int = 0


@dataclass
class StairState:
    """One stair (both directions share the width). `links` are its directed links."""

    id: str
    width_m: float
    bucket: Bucket
    links: list[Link]
    rooms: list[str]  # stairwell rooms it touches
    blocked: bool = False


@dataclass
class Event:
    id: str
    type: str
    t_start: float
    params: dict
    room: str | None = None
    exit: str | None = None
    door: str | None = None
    stair: str | None = None
    point: np.ndarray | None = None
    lv: int = 0
    phase: int = 0  # fire/smoke: 0 not started, 1 costly, 2 impassable; others: 1 fired
    tris: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    stairs: list[str] = field(default_factory=list)


class World:
    """Scene geometry, exits, doors, stairs, hazard costs and per-exit distance fields."""

    def __init__(self, scene: dict, dt: float) -> None:
        self.nav = NavMesh(scene)
        self.dt = dt
        self.lv_index = {lid: i for i, lid in enumerate(self.nav.level_ids)}
        self.rooms = {r["id"]: r for r in scene["rooms"]}
        self.room_lv = {rid: self.lv_index.get(r["level"], 0) for rid, r in self.rooms.items()}
        self.room_polys = {rid: Polygon(r["polygon"]).buffer(0) for rid, r in self.rooms.items()}
        self.door_by_id = {d["id"]: d for d in scene["doors"]}
        self.exits = self._build_exits(scene)
        self.doors = self._build_doors(scene)
        self.stairs = self._build_stairs()
        self.elevators = scene.get("elevators", [])  # ponytail: never used; nobody takes a lift in a fire
        self.fields: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.refresh_fields()
        self.base_cost = {e: f[0].copy() for e, f in self.fields.items()}  # hazard-free costs
        self.base_cost_flat = {e: f[0].copy() for e, f in self.fields_flat.items()}

    def _build_exits(self, scene: dict) -> dict[str, ExitState]:
        out = {}
        for e in scene["exits"]:
            lv = self.lv_index.get(e["level"], 0)
            goal = self.nav.snap(e["position"], lv)
            w = float(e.get("width_m") or 0.9)
            out[e["id"]] = ExitState(e["id"], e.get("label") or e["id"], np.asarray(e["position"], float), goal,
                                     int(self.nav.locate(goal[None], lv)[0]), w, Bucket(FLOW_P_S_M * w, self.dt),
                                     e["door_id"], lv)
        if not out:
            raise ValueError("scene has no exits")
        return out

    def _build_doors(self, scene: dict) -> list[DoorState]:
        exit_doors = {e["door_id"] for e in scene["exits"]}
        out = []
        for d in scene["doors"]:
            if d["id"] in exit_doors:
                continue
            lv = self.lv_index.get(d["level"], 0)
            seg = np.asarray(d["segment"], float)
            w = float(d.get("width_m") or np.linalg.norm(seg[1] - seg[0]))
            out.append(DoorState(d["id"], seg[0], seg[1], w, self.nav.tris_in(LineString(seg), lv), d["connects"],
                                 Bucket(FLOW_P_S_M * w, self.dt), lv))
        return out

    def _build_stairs(self) -> dict[str, StairState]:
        out: dict[str, StairState] = {}
        for link in self.nav.links.values():
            st = out.get(link.id)
            if st is None:
                rooms = [rid for rid, poly in self.room_polys.items() if self.rooms[rid]["type"] == "stairwell"
                         and self.room_lv[rid] in (link.a_lv, link.b_lv)
                         and (poly.buffer(0.5).covers(Point(link.a_pt)) or poly.buffer(0.5).covers(Point(link.b_pt)))]
                st = out[link.id] = StairState(link.id, link.width_m, Bucket(STAIR_FLOW_P_S_M * link.width_m, self.dt),
                                               [], rooms)
            st.links.append(link)
        return out

    # ---- routing ---------------------------------------------------------
    def refresh_fields(self) -> None:
        """Recompute each exit's cost-to-go field (with and without stairs) from current costs."""
        self.fields, self.lengths, self.first_stair = {}, {}, {}
        self.fields_flat, self.lengths_flat = {}, {}
        for eid, ex in self.exits.items():
            cost, nxt = self.nav.distance_field(ex.seed_tri)
            self.fields[eid] = (cost, nxt)
            self.lengths[eid], self.first_stair[eid] = self._walk_length(cost, nxt)
            cost_f, nxt_f = self.nav.distance_field(ex.seed_tri, use_links=False)
            self.fields_flat[eid] = (cost_f, nxt_f)
            self.lengths_flat[eid], _ = self._walk_length(cost_f, nxt_f)

    def _walk_length(self, cost: np.ndarray, nxt: np.ndarray) -> tuple[np.ndarray, list[str | None]]:
        """Meters along the cost-optimal tree and the first stair id on it, per triangle."""
        length = np.full(len(cost), INF)
        stair: list[str | None] = [None] * len(cost)
        for t in np.argsort(cost, kind="stable"):
            if not np.isfinite(cost[t]):
                break
            n = int(nxt[t])
            if n < 0:
                length[t] = 0.0
                continue
            length[t] = length[n] + self.nav.walk_len(t, n)
            link = self.nav.links.get((t, n))
            stair[t] = link.id if link else stair[n]
        return length, stair

    def route_cost(self, exit_id: str, tri: int, stairs_ok: bool = True) -> float:
        """Hazard-weighted route cost (m-equivalent) from triangle `tri` to the exit (inf if cut off)."""
        return float((self.fields if stairs_ok else self.fields_flat)[exit_id][0][tri])

    def through_hazard(self, exit_id: str, tri: int, stairs_ok: bool = True) -> bool:
        """True when hazards made the route to the exit costlier than it was at the start."""
        base = (self.base_cost if stairs_ok else self.base_cost_flat)[exit_id][tri]
        return bool(self.route_cost(exit_id, tri, stairs_ok) > base * 1.05 + 0.5)

    def route_m(self, exit_id: str, tri: int, stairs_ok: bool = True) -> float:
        """Walking length in m of the preferred route from `tri` to the exit (inf if cut off)."""
        return float((self.lengths if stairs_ok else self.lengths_flat)[exit_id][tri])

    def route_stair(self, exit_id: str, tri: int) -> str | None:
        """First stair id on the preferred route to the exit (None when it stays on this level)."""
        return self.first_stair[exit_id][tri]

    def path_to_exit(self, pos: np.ndarray, tri: int, exit_id: str, stairs_ok: bool = True) -> list[Waypoint] | None:
        """Waypoints from pos to the exit goal (may cross levels), or None when unreachable."""
        dist, nxt = (self.fields if stairs_ok else self.fields_flat)[exit_id]
        if not math.isfinite(dist[tri]):
            return None
        corr = self.nav.corridor(tri, nxt)
        return self.nav.waypoints(pos, self.exits[exit_id].goal, corr)

    def stair_link(self, stair_id: str, lv: int) -> Link | None:
        """The direction of a stair that starts on level lv."""
        st = self.stairs.get(stair_id)
        return next((ln for ln in st.links if ln.a_lv == lv), None) if st else None

    def path_to_point(self, pos: np.ndarray, lv: int, goal: np.ndarray, stairs_ok: bool = False) -> list[Waypoint] | None:
        """Waypoints to a point on the same level (A*); stairs only when stairs_ok."""
        ta, tb = int(self.nav.locate(pos[None], lv)[0]), int(self.nav.locate(goal[None], lv)[0])
        corr = self.nav.astar(ta, tb, use_links=stairs_ok)
        return None if corr is None else self.nav.waypoints(pos, goal, corr)

    def route_to_point_m(self, pos: np.ndarray, lv: int, goal: np.ndarray) -> float:
        """Rough same-level walking length (m) to a point: path length, or inf."""
        path = self.path_to_point(pos, lv, goal)
        if path is None:
            return INF
        pts = [pos] + [w.pt for w in path]
        return float(sum(np.linalg.norm(b - a) for a, b in zip(pts[:-1], pts[1:])))

    def room_at(self, pt: np.ndarray, lv: int = 0) -> str | None:
        p = Point(pt)
        return next((rid for rid, poly in self.room_polys.items() if self.room_lv[rid] == lv and poly.covers(p)), None)

    def room_point(self, room: str, rng: np.random.Generator, tries: int = 200) -> np.ndarray:
        """Uniform random walkable point inside a room (falls back to its snapped centroid)."""
        poly, lv = self.room_polys[room], self.room_lv[room]
        x0, z0, x1, z1 = poly.bounds
        pts = rng.uniform([x0, z0], [x1, z1], size=(tries, 2))
        ok = shapely.contains_xy(poly, pts[:, 0], pts[:, 1]) & shapely.contains_xy(self.nav.walk[lv], pts[:, 0], pts[:, 1])
        if ok.any():
            return pts[np.argmax(ok)]
        return self.nav.snap(poly.representative_point().coords[0], lv)

    # ---- events ----------------------------------------------------------
    def resolve_event(self, ev: dict) -> Event:
        """Turn a scenario event into an Event with its location resolved to ids, a point and a level."""
        at = ev.get("at") or {}
        out = Event(ev.get("id") or f"ev_{id(ev)}", ev["type"], float(ev.get("t_start_s", 0.0)),
                    dict(ev.get("params") or {}))
        if "room" in at:
            self._at_room(out, at["room"])
        elif "exit" in at:
            out.exit = at["exit"]
            if out.exit in self.exits:
                out.point, out.lv = self.exits[out.exit].goal, self.exits[out.exit].lv
        elif "door" in at:
            self._at_door(out, at["door"])
        elif "stair" in at:
            out.stair = at["stair"]
            link = next(iter(self.stairs[out.stair].links), None) if out.stair in self.stairs else None
            if link:
                out.point, out.lv, out.stairs = link.a_pt, link.a_lv, [out.stair]
        elif "point" in at:
            out.lv = self.lv_index.get(at.get("level"), 0)
            out.point = self.nav.snap(at["point"], out.lv)
            out.room = self.room_at(out.point, out.lv)
            if out.room:
                out.stairs = [s for s, st in self.stairs.items() if out.room in st.rooms]
        if out.type in ("fire", "smoke"):
            out.tris = self._hazard_tris(out)
        return out

    def _at_room(self, out: Event, room: str) -> None:
        out.room = room
        if room not in self.room_polys:
            log.warning("event %s: unknown room %s", out.id, room)
            return
        out.lv = self.room_lv[room]
        poly = self.room_polys[room].intersection(self.nav.walk[out.lv])
        src = poly if not poly.is_empty else self.room_polys[room]
        out.point = self.nav.snap(src.representative_point().coords[0], out.lv)
        out.stairs = [s for s, st in self.stairs.items() if room in st.rooms]

    def _at_door(self, out: Event, door: str) -> None:
        out.door = door
        d = self.door_by_id.get(door)
        ex = next((e for e in self.exits.values() if e.door_id == door), None)
        if ex:
            out.exit = ex.id
        if d:
            out.lv = self.lv_index.get(d["level"], 0)
            out.point = self.nav.snap(np.mean(d["segment"], axis=0), out.lv)

    def _hazard_tris(self, ev: Event) -> np.ndarray:
        if ev.room in self.room_polys:
            parts = [self.nav.tris_with_centroid_in(self.room_polys[ev.room], ev.lv)]
            parts += [d.tris for d in self.doors if ev.room in d.connects]
            return np.unique(np.concatenate(parts)).astype(int)
        if ev.point is not None:
            return self.nav.tris_in(Point(ev.point).buffer(3.0), ev.lv)
        return np.zeros(0, dtype=int)

    def set_cost(self, tris: np.ndarray, mult: float, stairs: list[str] | None = None) -> None:
        """Raise the entry cost of triangles and stairs (never lowers it) and refresh fields."""
        for sid in stairs or []:
            self.nav.stair_mult[sid] = max(self.nav.stair_mult.get(sid, 1.0), mult)
            if math.isinf(mult) and sid in self.stairs:
                self.stairs[sid].blocked = True
        if len(tris):
            self.nav.mult[tris] = np.maximum(self.nav.mult[tris], mult)
        if len(tris) or stairs:
            self.refresh_fields()

    def block_door(self, door_id: str) -> None:
        d = next((d for d in self.doors if d.id == door_id), None)
        if d is not None:
            self.set_cost(d.tris, INF)

    def block_stair(self, stair_id: str) -> None:
        if stair_id in self.stairs:
            self.set_cost(np.zeros(0, dtype=int), INF, [stair_id])
