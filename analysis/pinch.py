"""Pinch points: narrowest clear width on each room's route to its nearest exit.

Walkable is rasterized at 5 cm. Scene walkable is already eroded by the agent
radius (contract D6), so clear width = 2 * (EDT + agent_radius_m), accurate to
about one cell (+-5 cm). The route is the widest path (maximize the minimum
clearance) to the room's nearest target by walking distance; the bottleneck is
found by binary search on the clearance threshold with connected-component
labels, then the shortest path inside that threshold gives the route and the
pinch location. Targets are exits on the room's level, plus stair landings so
upper floors route to a stair. Walkable stops at an exit door, so the EDT
near it is cut by the dead end: the last TARGET_RADIUS_M of the approach is
not measured on the raster and the exit door's (or stair's) own width_m from
the scene stands in for it.
Flow uses 1.3 persons/s per m of clear width (SFPE maximum specific flow,
unverified), with no boundary-layer deduction, so it is an upper bound.
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon
from skimage.graph import MCP_Geometric

RES_M = 0.05
FLOW_P_S_M = 1.3
FLAG_BELOW_M = 0.9
TARGET_RADIUS_M = 1.0  # > half the widest corridor we expect, so the dead end never caps it


def _walk_union(scene: dict, level: str):
    polys = [Polygon(p["exterior"], p.get("holes", [])) for p in scene["walkable"].get(level, {}).get("polygons", [])]
    return shapely.union_all(polys) if polys else None


def _targets(scene: dict, level: str) -> list[tuple[str, str, object, float, list[float]]]:
    """(kind, id, zone, width_m, marker position) for exits and stair landings on a level.

    The zone is clipped to the room the exit or landing is in, so a target never
    leaks through a wall into a neighbouring room.
    """
    rooms = {r["id"]: Polygon(r["polygon"]).buffer(0.1) for r in scene["rooms"] if r["level"] == level}
    doors = {d["id"]: d for d in scene["doors"]}
    out = []
    for e in scene["exits"]:
        d = doors.get(e["door_id"])
        if e["level"] != level or not d:
            continue
        clip = shapely.union_all([rooms[c] for c in d["connects"] if c in rooms])
        out.append(("exit", e["id"], LineString(d["segment"]).buffer(TARGET_RADIUS_M).intersection(clip),
                    e["width_m"], e["position"]))
    for s in scene.get("stairs", []):
        for lv, pt in ((s["from_level"], s["bottom"]), (s["to_level"], s["top"])):
            clip = [g for g in rooms.values() if g.contains(Point(pt))] if lv == level else []
            if clip:
                out.append(("stair", s["id"], Point(pt).buffer(TARGET_RADIUS_M).intersection(clip[0]), s["width_m"], pt))
    return out


def _connected(mask: np.ndarray, seed: tuple[int, int], target: np.ndarray) -> bool:
    if not mask[seed]:
        return False
    lab, _ = ndimage.label(mask | target)
    return bool(np.any(lab[target & mask] == lab[seed]))


def _bottleneck(edt: np.ndarray, walk: np.ndarray, seed: tuple[int, int], target: np.ndarray) -> float | None:
    levels = np.unique(edt[walk])
    levels = levels[levels <= edt[seed]]
    lo, hi = 0, len(levels) - 1  # invariant: levels[lo] connects (if anything does)
    if hi < 0 or not _connected(walk & (edt >= levels[0]), seed, target):
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _connected(walk & (edt >= levels[mid]), seed, target):
            lo = mid
        else:
            hi = mid - 1
    return float(levels[lo])


def _route(mask: np.ndarray, seed, target: np.ndarray, edt: np.ndarray | None = None):
    """8-connected path from seed to any target cell inside mask; (path, length_m).

    With edt, steps cost more near walls (1 + (0.5 m / clearance)^2), so the path
    follows centerlines and its narrowest cell is a real pinch, not a wall hug.
    """
    step = np.ones(mask.shape) if edt is None else 1 + (0.5 / np.maximum(edt, RES_M)) ** 2
    mcp = MCP_Geometric(np.where(mask | target, step, np.inf))
    cost, _ = mcp.find_costs(list(zip(*np.nonzero(target))))
    if not np.isfinite(cost[seed]):
        return None, math.inf
    path = mcp.traceback(seed)
    length = sum(math.dist(a, b) for a, b in zip(path, path[1:])) * RES_M
    return path, length


def _level(scene: dict, level: str, r_agent: float) -> list[dict]:
    walk_geom = _walk_union(scene, level)
    rooms = [r for r in scene["rooms"] if r["level"] == level and r["type"] != "outside"]
    if walk_geom is None or walk_geom.is_empty:
        return [{"room": r["id"], "error": "no walkable area on level"} for r in rooms]
    x0, z0, x1, z1 = walk_geom.bounds
    nx_, nz_ = int(math.ceil((x1 - x0) / RES_M)) + 2, int(math.ceil((z1 - z0) / RES_M)) + 2
    xs = x0 + (np.arange(nx_) - 0.5) * RES_M
    zs = z0 + (np.arange(nz_) - 0.5) * RES_M
    X, Z = np.meshgrid(xs, zs)
    walk = shapely.contains_xy(walk_geom, X, Z)
    edt = ndimage.distance_transform_edt(walk) * RES_M
    targets = []
    for kind, tid, zone, width, pos in _targets(scene, level):
        cells = shapely.contains_xy(zone, X, Z) & walk
        if cells.any():
            targets.append((kind, tid, cells, width, pos))
    out = []
    for r in rooms:
        inside = shapely.contains_xy(Polygon(r["polygon"]), X, Z) & walk
        if not inside.any():
            out.append({"room": r["id"], "error": "no walkable cells in room"})
            continue
        seed = np.unravel_index(np.argmax(np.where(inside, edt, -1)), edt.shape)
        dists = [(_route(walk, seed, t[2])[1], t) for t in targets]
        dist, (kind, tid, cells, t_width, t_pos) = min(dists, key=lambda d: d[0], default=(math.inf, (None,) * 5))
        if not math.isfinite(dist):
            out.append({"room": r["id"], "error": "no route to an exit or stair on this level"})
            continue
        b = _bottleneck(edt, walk, seed, cells)
        path, length = _route(walk & (edt >= b), seed, cells, edt)
        measured = [c for c in path if not cells[c]]
        width, at, by = float(t_width), list(t_pos), "target width_m"
        if measured:
            i, j = min(measured, key=lambda c: edt[c])
            if 2 * (edt[i, j] + r_agent) < width:
                width, at, by = 2 * (edt[i, j] + r_agent), [float(xs[j]), float(zs[i])], "clearance raster"
        out.append({"room": r["id"], "target": {"kind": kind, "id": tid},
                    "nearest_route_m": round(dist, 2), "widest_route_m": round(length, 2),
                    "pinch_width_m": round(width, 2), "pinch_at": [round(v, 2) for v in at], "pinch_from": by,
                    "flow_p_per_min": round(FLOW_P_S_M * width * 60), "below_0_9_m": bool(width < FLAG_BELOW_M)})
    return out


def analyze(scene: dict) -> dict:
    r_agent = scene["navmesh"]["agent_radius_m"]
    rooms = [row for lv in scene["levels"] for row in _level(scene, lv["id"], r_agent)]
    ok = [r for r in rooms if "pinch_width_m" in r]
    return {"rooms": rooms, "res_m": RES_M, "flow_p_s_m": FLOW_P_S_M, "flag_below_m": FLAG_BELOW_M,
            "narrowest": min(ok, key=lambda r: r["pinch_width_m"])["room"] if ok else None,
            "flagged": [r["room"] for r in ok if r["below_0_9_m"]]}
