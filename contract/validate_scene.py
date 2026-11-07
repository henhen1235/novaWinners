"""Semantic validator for scene.json (contract schema_version 1.0).

Codes, severities and thresholds follow DECISIONS.md D3 to D5 and D17. Coordinates are
meters, Y up; 2D points are [x, z].

    python -m contract.validate_scene scene.json [--json]

Exit status is 0 only when no error-severity issue was found.
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Callable

import jsonschema
import networkx as nx
import numpy as np
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from shapely.geometry import LineString, Point, Polygon

from contract.models import SCHEMA_PATH

OUTSIDE = "outside"
ID_COLLECTIONS = (
    "levels", "walls", "rooms", "doors", "exits", "stairs",
    "elevators", "obstacles", "points_of_interest", "links",
)
LEVELED_COLLECTIONS = ("walls", "rooms", "doors", "exits", "obstacles", "points_of_interest")
POLYGON_COLLECTIONS = ("walls", "rooms", "stairs", "obstacles")
MIN_POLYGON_AREA_M2 = 0.01
MIN_DOOR_SEGMENT_M = 0.01
EXTENT_RANGE_M = (3.0, 500.0)
DOOR_WIDTH_RANGE_M = (0.6, 3.0)
SMALL_ROOM_M2 = 1.0
ROOM_POINT_TOL_M = 0.5
LINK_TRIANGLE_TOL_M = 0.5
EXIT_TRIANGLE_TOL_M = 1.0
DEGENERATE_TRIANGLE_M2 = 1e-6
NAVMESH_AREA_REL_TOL = 0.02
SCALE_DOOR_MEDIAN_RANGE_M = (0.7, 1.3)
DOOR_ROOM_TOL_M = 0.3
LEAK_REACH_M = 0.30
LEAK_TOUCH_M = 0.02
MAX_SCHEMA_ISSUES = 20

Issue = dict


def _issue(code: str, severity: str, message: str, ref: str | None = None) -> Issue:
    return {"code": code, "severity": severity, "message": message, "ref": ref}


def _err(code: str, message: str, ref: str | None = None) -> Issue:
    return _issue(code, "error", message, ref)


def _warn(code: str, message: str, ref: str | None = None) -> Issue:
    return _issue(code, "warn", message, ref)


def _poly(exterior: list, holes: list | None = None) -> Polygon | None:
    """Shapely polygon from contract rings, or None if it cannot be built."""
    try:
        return Polygon(exterior, holes or [])
    except Exception:  # noqa: BLE001 - malformed coordinates are reported by the caller
        return None


def _walkable_polygons(scene: dict) -> list[tuple[str, str, Polygon | None]]:
    """(level, ref, polygon) for every walkable polygon."""
    return [
        (level, f"walkable.{level}.polygons[{i}]", _poly(wp["exterior"], wp.get("holes", [])))
        for level, wl in scene["walkable"].items()
        for i, wp in enumerate(wl["polygons"])
    ]


# --- 1. schema ---------------------------------------------------------------


@lru_cache(maxsize=1)
def _schema_validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def check_schema(scene: object) -> list[Issue]:
    """JSON Schema (draft 2020-12) errors, capped at MAX_SCHEMA_ISSUES."""
    errors = sorted(_schema_validator().iter_errors(scene), key=lambda e: list(map(str, e.absolute_path)))
    issues = [
        _err("SCHEMA_INVALID", e.message[:300], "/" + "/".join(map(str, e.absolute_path)))
        for e in errors[:MAX_SCHEMA_ISSUES]
    ]
    if len(errors) > MAX_SCHEMA_ISSUES:
        issues.append(_err("SCHEMA_INVALID", f"{len(errors) - MAX_SCHEMA_ISSUES} more schema errors"))
    return issues


# --- 2. ids and references ---------------------------------------------------


def check_ids(scene: dict) -> list[Issue]:
    """DUPLICATE_ID: ids must be unique across all collections."""
    seen: dict[str, str] = {}
    issues = []
    for coll in ID_COLLECTIONS:
        for item in scene.get(coll, []):
            if item["id"] in seen:
                issues.append(_err("DUPLICATE_ID", f"id used in {seen[item['id']]} and {coll}", item["id"]))
            seen.setdefault(item["id"], coll)
    return issues


def _level_refs(scene: dict) -> list[tuple[str, str]]:
    """(owner ref, level id) for every level reference in the scene."""
    refs = [(item["id"], item["level"]) for coll in LEVELED_COLLECTIONS for item in scene.get(coll, [])]
    refs += [(s["id"], s[k]) for s in scene.get("stairs", []) for k in ("from_level", "to_level")]
    refs += [(e["id"], lvl) for e in scene.get("elevators", []) for lvl in e["levels"]]
    refs += [(lk["id"], lk[end]["level"]) for lk in scene.get("links", []) for end in ("from", "to")]
    refs += [(f"walkable.{lvl}", lvl) for lvl in scene["walkable"]]
    refs += [("navmesh.triangle_level", lvl) for lvl in sorted(set(scene["navmesh"]["triangle_level"]))]
    return refs


def check_references(scene: dict) -> list[Issue]:
    """BAD_REFERENCE: unknown level / room / door ids, doors joining rooms on other levels."""
    levels = {lv["id"] for lv in scene["levels"]}
    room_level = {r["id"]: r["level"] for r in scene.get("rooms", [])}
    door_ids = {d["id"] for d in scene.get("doors", [])}
    issues = [_err("BAD_REFERENCE", f"unknown level {lvl!r}", ref) for ref, lvl in _level_refs(scene) if lvl not in levels]
    for door in scene.get("doors", []):
        for target in door["connects"]:
            if target != OUTSIDE and target not in room_level:
                issues.append(_err("BAD_REFERENCE", f"connects unknown room {target!r}", door["id"]))
            elif target != OUTSIDE and room_level[target] != door["level"]:
                issues.append(_err("BAD_REFERENCE", f"connects room {target!r} on level {room_level[target]!r}", door["id"]))
        toward = door.get("opens_toward")
        if toward is not None and toward != OUTSIDE and toward not in room_level:
            issues.append(_err("BAD_REFERENCE", f"opens_toward unknown room {toward!r}", door["id"]))
    for ex in scene.get("exits", []):
        if ex["door_id"] not in door_ids:
            issues.append(_err("BAD_REFERENCE", f"unknown door {ex['door_id']!r}", ex["id"]))
    return issues


# --- 3. polygons -------------------------------------------------------------


def _polygon_issue(poly: Polygon | None, ref: str) -> Issue | None:
    if poly is None:
        return _err("INVALID_POLYGON", "polygon cannot be constructed", ref)
    if not poly.is_valid:
        return _err("INVALID_POLYGON", f"invalid polygon: {shapely.is_valid_reason(poly)}", ref)
    if not poly.area > MIN_POLYGON_AREA_M2:
        return _err("INVALID_POLYGON", f"area {poly.area:.4f} m^2 <= {MIN_POLYGON_AREA_M2}", ref)
    return None


def check_polygons(scene: dict) -> list[Issue]:
    """INVALID_POLYGON for walls, rooms, stairs, obstacles, walkable, short door segments."""
    candidates = [(item["id"], _poly(item["polygon"])) for c in POLYGON_COLLECTIONS for item in scene.get(c, [])]
    candidates += [(ref, poly) for _, ref, poly in _walkable_polygons(scene)]
    issues = [i for ref, poly in candidates if (i := _polygon_issue(poly, ref))]
    for door in scene.get("doors", []):
        (x0, z0), (x1, z1) = door["segment"]
        if not np.hypot(x1 - x0, z1 - z0) >= MIN_DOOR_SEGMENT_M:
            issues.append(_err("INVALID_POLYGON", "door segment shorter than 1 cm", door["id"]))
    return issues


# --- 4. extent ---------------------------------------------------------------


def check_extent(scene: dict) -> list[Issue]:
    """EXTENT_OUT_OF_RANGE: longest bbox side of walls, rooms and walkable in [3, 500] m."""
    points = [p for c in ("walls", "rooms") for item in scene.get(c, []) for p in item["polygon"]]
    points += [p for wl in scene["walkable"].values() for wp in wl["polygons"] for p in wp["exterior"]]
    pts = np.asarray(points, float).reshape(-1, 2)
    longest = float(np.max(np.ptp(pts, axis=0))) if len(pts) else 0.0
    lo, hi = EXTENT_RANGE_M
    if not lo <= longest <= hi:
        return [_err("EXTENT_OUT_OF_RANGE", f"longest extent {longest:.2f} m outside [{lo}, {hi}] m")]
    return []


# --- 5. doors and rooms ------------------------------------------------------


def check_sizes(scene: dict) -> list[Issue]:
    """DOOR_TOO_NARROW (width outside [0.6, 3.0] m) and SMALL_ROOM_ARTIFACT (< 1 m^2)."""
    lo, hi = DOOR_WIDTH_RANGE_M
    issues = [
        _err("DOOR_TOO_NARROW", f"width {d['width_m']} m outside [{lo}, {hi}] m", d["id"])
        for d in scene.get("doors", [])
        if not lo <= d["width_m"] <= hi
    ]
    for room in scene.get("rooms", []):
        poly = _poly(room["polygon"])
        if poly is not None and abs(poly.area) < SMALL_ROOM_M2:
            issues.append(_warn("SMALL_ROOM_ARTIFACT", f"room area {poly.area:.2f} m^2 < {SMALL_ROOM_M2}", room["id"]))
    return issues


# --- 6. exits ----------------------------------------------------------------


def check_exits(scene: dict) -> list[Issue]:
    """NO_EXITS, and EXIT_UNREACHABLE (error) for an exit whose door connects no room (D17)."""
    if not scene.get("exits"):
        return [_err("NO_EXITS", "scene has no exits")]
    rooms = {r["id"] for r in scene.get("rooms", [])}
    doors = {d["id"]: d for d in scene.get("doors", [])}
    return [
        _err("EXIT_UNREACHABLE", f"exit door {ex['door_id']!r} connects no room", ex["id"])
        for ex in scene["exits"]
        if ex["door_id"] in doors and not rooms & set(doors[ex["door_id"]]["connects"])
    ]


def check_door_placement(scene: dict) -> list[Issue]:
    """UNKNOWN_SPACE_NEAR_DOOR: a door segment more than 0.3 m from a room it connects (D17)."""
    rooms = {r["id"]: _poly(r["polygon"]) for r in scene.get("rooms", [])}
    issues = []
    for door in scene.get("doors", []):
        segment = LineString(door["segment"])
        for room_id in door["connects"]:
            poly = rooms.get(room_id)
            if poly is None or poly.is_empty:
                continue
            gap = segment.distance(poly.boundary)
            if gap > DOOR_ROOM_TOL_M:
                issues.append(_warn("UNKNOWN_SPACE_NEAR_DOOR", f"door is {gap:.2f} m from room {room_id}", door["id"]))
    return issues


def check_leaks(scene: dict) -> list[Issue]:
    """LEAKY_WALL_SUSPECTED: walkable joins two rooms of a level that share no door (D17).

    A room's region is the walkable within 0.30 m of its polygon: that reaches
    through a normal wall gap, while walkable eroded 0.25 m from each side of a
    solid wall stays apart. Regions closer than 2 cm count as joined.
    """
    walk: dict[str, list[Polygon]] = {}
    for level, _, poly in _walkable_polygons(scene):
        if poly is not None and poly.is_valid:
            walk.setdefault(level, []).append(poly)
    union = {level: shapely.union_all(polys) for level, polys in walk.items()}
    regions = []
    for room in scene.get("rooms", []):
        poly = _poly(room["polygon"])
        if poly is None or not poly.is_valid or room["level"] not in union:
            continue
        region = union[room["level"]].intersection(poly.buffer(LEAK_REACH_M))
        if not region.is_empty:
            regions.append((room["id"], room["level"], region))
    door_pairs = {frozenset(d["connects"]) for d in scene.get("doors", [])}
    issues = []
    # ponytail: O(rooms^2) shapely tests, fine below ~100 rooms per scene; STRtree if that grows.
    for i, (a, level_a, region_a) in enumerate(regions):
        grown = region_a.buffer(LEAK_TOUCH_M)
        for b, level_b, region_b in regions[i + 1:]:
            if level_a == level_b and frozenset((a, b)) not in door_pairs and grown.intersects(region_b):
                issues.append(_warn("LEAKY_WALL_SUSPECTED", f"walkable joins {a} and {b} without a door", a))
    return issues


# --- 7. room graph (D4) ------------------------------------------------------


def _room_at(rooms: list[tuple[str, str, Polygon | None]], level: str, xz: list) -> str | None:
    """Id of the nearest room on `level` within 0.5 m of point xz, else None."""
    pt = Point(xz)
    best: tuple[float, str] | None = None
    for room_id, room_level, poly in rooms:
        if room_level != level or poly is None:
            continue
        dist = poly.distance(pt)
        if dist <= ROOM_POINT_TOL_M and (best is None or dist < best[0]):
            best = (dist, room_id)
    return best[1] if best else None


def _room_graph(scene: dict, rooms: list[tuple[str, str, Polygon | None]]) -> tuple[nx.DiGraph, list[Issue]]:
    graph = nx.DiGraph()
    graph.add_node(OUTSIDE)
    graph.add_nodes_from(r[0] for r in rooms)
    for door in scene.get("doors", []):
        a, b = door["connects"]
        if a in graph and b in graph:
            graph.add_edges_from([(a, b), (b, a)])
    issues = []
    for stair in scene.get("stairs", []):
        ends = [(stair["from_level"], stair["bottom"], "bottom"), (stair["to_level"], stair["top"], "top")]
        found = [_room_at(rooms, lvl, xz) for lvl, xz, _ in ends]
        for (lvl, _, name), room in zip(ends, found):
            if room is None:
                issues.append(_warn("ORPHAN_STAIR", f"stair {name} is not inside a room on {lvl!r}", stair["id"]))
        if None not in found:
            graph.add_edges_from([(found[0], found[1]), (found[1], found[0])])
    for link in scene.get("links", []):
        a = _room_at(rooms, link["from"]["level"], link["from"]["position"])
        b = _room_at(rooms, link["to"]["level"], link["to"]["position"])
        if a and b:
            graph.add_edges_from([(a, b), (b, a)] if link["bidirectional"] else [(a, b)])
        else:
            issues.append(_warn("ORPHAN_STAIR", "link end is not inside a room", link["id"]))
    return graph, issues


def check_room_graph(scene: dict) -> list[Issue]:
    """ROOM_UNREACHABLE (no path to an exit door's rooms) and ORPHAN_STAIR."""
    rooms = [(r["id"], r["level"], _poly(r["polygon"])) for r in scene.get("rooms", [])]
    graph, issues = _room_graph(scene, rooms)
    doors = {d["id"]: d for d in scene.get("doors", [])}
    goals = {c for ex in scene.get("exits", []) if ex["door_id"] in doors for c in doors[ex["door_id"]]["connects"]}
    reverse = graph.reverse(copy=False)
    reachable = {n for g in goals if g in graph for n in nx.descendants(reverse, g) | {g}}
    issues += [
        _err("ROOM_UNREACHABLE", "no door/stair path to an exit", room_id)
        for room_id, _, _ in rooms
        if room_id not in reachable
    ]
    return issues


# --- 8. navmesh (D5) ---------------------------------------------------------


def _cross(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]


def _segment_distance(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab, ap = b - a, p - a
    denom = (ab * ab).sum(-1)
    t = np.divide((ap * ab).sum(-1), denom, out=np.zeros_like(denom), where=denom > 0)
    return np.linalg.norm(ap - np.clip(t, 0.0, 1.0)[:, None] * ab, axis=-1)


def point_triangle_distance(p: np.ndarray, tris: np.ndarray, areas: np.ndarray) -> np.ndarray:
    """XZ distance (m) from point p (2,) to each triangle in tris (N, 3, 2); 0 inside.

    `areas` are the unsigned triangle areas; degenerate ones are never "inside".
    """
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    d1, d2, d3 = _cross(b - a, p - a), _cross(c - b, p - b), _cross(a - c, p - c)
    inside = (((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))) & (areas > 0)
    edge = np.minimum(np.minimum(_segment_distance(p, a, b), _segment_distance(p, b, c)), _segment_distance(p, c, a))
    return np.where(inside, 0.0, edge)


def _navmesh_structure(scene: dict) -> tuple[list[Issue], np.ndarray, np.ndarray, list[str]]:
    """Structural navmesh checks. Returns issues, vertices (V, 3), triangles (T, 3), levels."""
    nav = scene["navmesh"]
    levels = nav["triangle_level"]
    try:
        verts = np.asarray(nav["vertices"], float).reshape(-1, 3)
        tris = np.asarray(nav["triangles"], np.int64).reshape(-1, 3)
    except (OverflowError, ValueError) as exc:
        return [_err("NAVMESH_INVALID", f"unreadable vertices/triangles: {exc}", "navmesh")], np.empty((0, 3)), np.empty((0, 3), np.int64), []
    issues = []
    if not np.isfinite(verts).all():
        issues.append(_err("NAVMESH_INVALID", "vertices contain NaN or infinite coordinates", "navmesh"))
    if len(levels) != len(tris):
        issues.append(_err("NAVMESH_INVALID", f"triangle_level has {len(levels)} entries for {len(tris)} triangles", "navmesh"))
    bad = int(((tris < 0) | (tris >= len(verts))).any(axis=1).sum())
    if bad:
        issues.append(_err("NAVMESH_INVALID", f"{bad} triangles index outside 0..{len(verts) - 1}", "navmesh"))
    known = {lv["id"] for lv in scene["levels"]}
    issues += [_err("NAVMESH_INVALID", f"triangle_level uses unknown level {lvl!r}", "navmesh") for lvl in sorted(set(levels) - known)]
    return issues, verts, tris, levels


def _components(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Component label per triangle; triangles sharing a 1 mm-rounded vertex position are connected."""
    _, pos = np.unique(np.round(verts * 1000).astype(np.int64), axis=0, return_inverse=True)
    pos = pos.reshape(-1)
    n_t, n_p = len(tris), int(pos.max()) + 1
    rows = np.repeat(np.arange(n_t), 3)
    cols = n_t + pos[tris].reshape(-1)
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(n_t + n_p, n_t + n_p))
    return connected_components(graph, directed=False)[1][:n_t]


class _TriangleIndex:
    """Per-level XZ triangle arrays for distance queries."""

    def __init__(self, xz: np.ndarray, areas: np.ndarray, levels: np.ndarray) -> None:
        self.xz, self.areas, self.levels = xz, areas, levels

    def near(self, level: str, point: list, tol: float) -> np.ndarray:
        """Indices of triangles on `level` within tol meters of [x, z] `point`."""
        idx = np.flatnonzero(self.levels == level)
        if not len(idx):
            return idx
        d = point_triangle_distance(np.asarray(point, float), self.xz[idx], self.areas[idx])
        return idx[d <= tol]


def _area_issues(scene: dict, areas: np.ndarray, levels: np.ndarray) -> list[Issue]:
    walk: dict[str, float] = {lvl: 0.0 for lvl in scene["walkable"]}
    for lvl, _, poly in _walkable_polygons(scene):
        walk[lvl] += abs(poly.area) if poly is not None else 0.0
    issues = []
    for lvl in sorted(set(walk) | set(levels.tolist())):
        nav_area, walk_area = float(areas[levels == lvl].sum()), walk.get(lvl, 0.0)
        if abs(nav_area - walk_area) > NAVMESH_AREA_REL_TOL * walk_area + 1e-9:
            issues.append(_err("NAVMESH_INVALID", f"navmesh area {nav_area:.2f} m^2 vs walkable {walk_area:.2f} m^2 (> 2%)", lvl))
    return issues


def _join_graph(scene: dict, comp: np.ndarray, index: _TriangleIndex) -> tuple[nx.Graph, list[Issue]]:
    """Graph over triangle components plus a "__exit__" node (D5)."""
    graph = nx.Graph()
    graph.add_nodes_from(range(int(comp.max()) + 1))
    graph.add_node("__exit__")
    ends = [((s["from_level"], s["bottom"]), (s["to_level"], s["top"])) for s in scene.get("stairs", [])]
    ends += [((lk["from"]["level"], lk["from"]["position"]), (lk["to"]["level"], lk["to"]["position"])) for lk in scene.get("links", [])]
    for a, b in ends:
        near_a, near_b = index.near(*a, LINK_TRIANGLE_TOL_M), index.near(*b, LINK_TRIANGLE_TOL_M)
        if len(near_a) and len(near_b):
            nx.add_path(graph, sorted(set(comp[near_a].tolist()) | set(comp[near_b].tolist())))
    issues = []
    for ex in scene.get("exits", []):
        near = index.near(ex["level"], ex["position"], EXIT_TRIANGLE_TOL_M)
        if not len(near):
            issues.append(_warn("EXIT_UNREACHABLE", "no navmesh triangle within 1.0 m of the exit", ex["id"]))
        graph.add_edges_from(("__exit__", c) for c in set(comp[near].tolist()))
    return graph, issues


def _island_issues(graph: nx.Graph, comp: np.ndarray, index: _TriangleIndex) -> list[Issue]:
    issues = []
    for group in nx.connected_components(graph):
        if "__exit__" in group:
            continue
        tri = np.flatnonzero(np.isin(comp, list(group)))
        center = index.xz[tri].reshape(-1, 2).mean(axis=0)
        level = str(index.levels[tri[0]])
        msg = f"{len(tri)} triangles ({index.areas[tri].sum():.2f} m^2) near [{center[0]:.2f}, {center[1]:.2f}] cannot reach an exit"
        issues.append(_err("NAVMESH_ISLAND", msg, level))
    return issues


def check_navmesh(scene: dict) -> list[Issue]:
    """NAVMESH_INVALID, NAVMESH_ISLAND, EXIT_UNREACHABLE."""
    issues, verts, tris, level_list = _navmesh_structure(scene)
    if issues:
        return issues
    xz = verts[:, [0, 2]][tris] if len(tris) else np.empty((0, 3, 2))
    areas = 0.5 * np.abs(_cross(xz[:, 1] - xz[:, 0], xz[:, 2] - xz[:, 0])) if len(tris) else np.empty(0)
    levels = np.asarray(level_list, dtype=object)
    degenerate = np.flatnonzero(areas < DEGENERATE_TRIANGLE_M2)
    if len(degenerate):
        issues.append(_err("NAVMESH_INVALID", f"{len(degenerate)} degenerate triangles (first: {degenerate[0]})", "navmesh"))
    issues += _area_issues(scene, areas, levels)
    if not len(tris):
        return issues + [_warn("EXIT_UNREACHABLE", "navmesh has no triangles", ex["id"]) for ex in scene.get("exits", [])]
    index = _TriangleIndex(xz, areas, levels)
    comp = _components(verts, tris)
    graph, exit_issues = _join_graph(scene, comp, index)
    return issues + exit_issues + _island_issues(graph, comp, index)


# --- 9. scale ----------------------------------------------------------------


def check_scale(scene: dict) -> list[Issue]:
    """SCALE_LOW_CONFIDENCE: median door width outside [0.7, 1.3] m."""
    widths = [d["width_m"] for d in scene.get("doors", [])]
    lo, hi = SCALE_DOOR_MEDIAN_RANGE_M
    if widths and not lo <= float(np.median(widths)) <= hi:
        return [_warn("SCALE_LOW_CONFIDENCE", f"median door width {np.median(widths):.2f} m outside [{lo}, {hi}] m")]
    return []


CHECKS: tuple[Callable[[dict], list[Issue]], ...] = (
    check_ids, check_references, check_polygons, check_extent, check_sizes,
    check_exits, check_door_placement, check_leaks, check_room_graph, check_navmesh, check_scale,
)


def validate(scene: dict) -> list[dict]:
    """All issues for `scene`, each {code, severity: "warn"|"error", message, ref}.

    Schema errors stop further checks. Each later check runs independently; an
    unexpected exception inside one becomes a CHECK_FAILED error instead of
    aborting validation.
    """
    issues = check_schema(scene)
    if issues:
        return issues
    for check in CHECKS:
        try:
            issues += check(scene)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            issues.append(_err("CHECK_FAILED", f"{check.__name__} raised {type(exc).__name__}: {exc}"))
    return issues


def _load(path: str) -> tuple[object | None, list[Issue]]:
    try:
        return json.loads(Path(path).read_text()), []
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, [_err("SCHEMA_INVALID", f"cannot read JSON: {exc}", path)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a scene.json against contract 1.0.")
    parser.add_argument("scene", help="path to scene.json")
    parser.add_argument("--json", action="store_true", help="print issues as JSON")
    args = parser.parse_args(argv)
    scene, issues = _load(args.scene)
    if not issues:
        issues = validate(scene)
    errors = sum(i["severity"] == "error" for i in issues)
    warnings = len(issues) - errors
    if args.json:
        print(json.dumps({"ok": errors == 0, "errors": errors, "warnings": warnings, "issues": issues}, indent=1))
    else:
        for i in issues:
            print(f"{i['severity']:5} {i['code']} [{i['ref'] or '-'}] {i['message']}")
        print(f"{args.scene}: {'OK' if errors == 0 else 'FAIL'} ({errors} errors, {warnings} warnings)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
