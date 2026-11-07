"""Contract scenes from compact Manhattan layout specs, plus the fixtures.

Frame: meters, Y up, 2D points [x, z]. A layout spec is a dict:

    name, wall_thickness_m (0.15), agent_radius_m (0.25),
    levels:  [{id, name, elevation_m, ceiling_height_m}]
    rooms:   [{id, level, name, type, rect: [x0, z0, x1, z1], has_window?}]
             rect is the WALL CENTERLINE box; the room polygon is it shrunk by t/2.
    doors:   [{level, center: [x, z], axis: "x"|"z", width,
               exit?: {label, leads_to, assembly_point?}}]
             axis "x": the door lies on a horizontal wall (z = const).
    obstacles: [{level, rect, height, kind}]
    stairs:  [{from_level, to_level, rect, bottom: [x, z], top: [x, z]}]
    elevators: [{levels, position, usable_in_fire}]
    pois:    [{level, position, kind}]

    python -m tools.layouts      # writes fixtures/*.scene.json and fixtures/negative/
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import numpy as np
from shapely.geometry import MultiPolygon, Point, Polygon, box

from ingest_lidar.geometry import (
    door_gap_box,
    navmesh_from_walkable,
    ring,
    walkable_from_parts,
    walkable_to_contract,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
FIXED_TIME = "1970-01-01T00:00:00Z"
PROBE_EXTRA_M = 0.1
DOUBLE_DOOR_MIN_M = 1.5
STAIR_COST = 2.0
EPS = 1e-9


def _git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "nogit"
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "nogit"


def _round(obj):
    """Recursively round floats to 1 mm (3 decimals), turning -0.0 into 0.0."""
    if isinstance(obj, float):
        return round(obj, 3) + 0.0
    if isinstance(obj, dict):
        return {k: _round(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v) for v in obj]
    return obj


# --- walls -------------------------------------------------------------------


def _line_key(axis: str, value: float) -> tuple[str, float]:
    """("h", z) for a horizontal wall line z = const, ("v", x) for x = const."""
    return axis, round(value, 3)


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1] + EPS:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _subtract(intervals: list[tuple[float, float]], cuts: list[tuple[float, float]]) -> list[tuple[float, float, bool, bool]]:
    """Intervals minus door cuts as (a, b, a_is_door_end, b_is_door_end)."""
    pieces = []
    for a, b in intervals:
        start, start_door = a, False
        for c0, c1 in sorted(cuts):
            if c1 <= start or c0 >= b:
                continue
            if c0 - start > EPS:
                pieces.append((start, c0, start_door, True))
            start, start_door = max(start, c1), True
        if b - start > EPS:
            pieces.append((start, b, start_door, False))
    return pieces


def _door_cut(door: dict) -> tuple[tuple[str, float], tuple[float, float]]:
    (cx, cz), half = door["center"], door["width"] / 2
    if door["axis"] == "x":
        return _line_key("h", cz), (cx - half, cx + half)
    return _line_key("v", cx), (cz - half, cz + half)


def _wall_lines(rects: list[list[float]]) -> dict[tuple[str, float], list[tuple[float, float]]]:
    lines: dict[tuple[str, float], list[tuple[float, float]]] = {}
    for x0, z0, x1, z1 in rects:
        for key, span in (
            (_line_key("h", z0), (x0, x1)), (_line_key("h", z1), (x0, x1)),
            (_line_key("v", x0), (z0, z1)), (_line_key("v", x1), (z0, z1)),
        ):
            lines.setdefault(key, []).append(span)
    return lines


def _walls(level: dict, rects: list[list[float]], doors: list[dict], t: float) -> list[dict]:
    """Wall rectangles of thickness t along every room-rect edge line, minus door gaps.

    Ends that are not door ends extend by t/2 so corners close.
    """
    lines = _wall_lines(rects)
    cuts: dict[tuple[str, float], list[tuple[float, float]]] = {}
    for door in doors:
        key, cut = _door_cut(door)
        if key not in lines:
            raise ValueError(f"door at {door['center']} is not on a wall line")
        cuts.setdefault(key, []).append(cut)
    walls = []
    for (axis, c), spans in sorted(lines.items()):
        for a, b, a_door, b_door in _subtract(_merge(spans), cuts.get((axis, c), [])):
            lo, hi = a - (0 if a_door else t / 2), b + (0 if b_door else t / 2)
            rect = box(lo, c - t / 2, hi, c + t / 2) if axis == "h" else box(c - t / 2, lo, c + t / 2, hi)
            walls.append({
                "id": f"wall_{level['id']}_{len(walls) + 1:03d}", "level": level["id"],
                "polygon": ring(rect), "height_m": level["ceiling_height_m"],
            })
    return walls


# --- rooms and doors ---------------------------------------------------------


def _room_polygon(rect: list[float], t: float) -> Polygon:
    x0, z0, x1, z1 = rect
    return box(x0 + t / 2, z0 + t / 2, x1 - t / 2, z1 - t / 2)


def _room(spec: dict, t: float) -> dict:
    poly = _room_polygon(spec["rect"], t)
    return {
        "id": spec["id"], "level": spec["level"], "name": spec["name"], "type": spec["type"],
        "polygon": ring(poly), "area_m2": poly.area,
        "centroid": [poly.centroid.x, poly.centroid.y], "has_window": spec.get("has_window", False),
    }


def _segment(door: dict) -> list[list[float]]:
    (cx, cz), half = door["center"], door["width"] / 2
    if door["axis"] == "x":
        return [[cx - half, cz], [cx + half, cz]]
    return [[cx, cz - half], [cx, cz + half]]


def _side_of(rooms: list[tuple[str, Polygon]], point: tuple[float, float]) -> str:
    pt = Point(point)
    return next((rid for rid, poly in rooms if poly.covers(pt)), "outside")


def _door(door: dict, door_id: str, rooms: list[tuple[str, Polygon]], t: float) -> dict:
    """Contract door; `connects` probes t/2 + 0.1 m to both sides of the wall."""
    (cx, cz), d = door["center"], t / 2 + PROBE_EXTRA_M
    probes = [(cx, cz - d), (cx, cz + d)] if door["axis"] == "x" else [(cx - d, cz), (cx + d, cz)]
    connects = [_side_of(rooms, p) for p in probes]
    if connects[0] == connects[1]:
        raise ValueError(f"door {door_id} at {door['center']} has {connects[0]!r} on both sides")
    is_exit = "exit" in door
    return {
        "id": door_id, "level": door["level"], "segment": _segment(door), "width_m": door["width"],
        "connects": connects, "is_exit": is_exit,
        "kind": "double" if door["width"] >= DOUBLE_DOOR_MIN_M else "swing",
        "fire_rated": None, "opens_toward": "outside" if is_exit else None,
    }


def _exit(door: dict, contract_door: dict, exit_id: str) -> dict:
    info = door["exit"]
    return {
        "id": exit_id, "level": door["level"], "door_id": contract_door["id"],
        "position": list(door["center"]), "width_m": door["width"],
        "leads_to": info.get("leads_to", "outside"), "label": info["label"],
        "assembly_point": info.get("assembly_point"),
    }


# --- stairs, walkable, scene -------------------------------------------------


def _stair(spec: dict, stair_id: str) -> dict:
    x0, z0, x1, z1 = spec["rect"]
    return {
        "id": stair_id, "from_level": spec["from_level"], "to_level": spec["to_level"],
        "polygon": ring(box(x0, z0, x1, z1)), "bottom": spec["bottom"], "top": spec["top"],
        "width_m": spec.get("width", min(x1 - x0, z1 - z0)), "direction": "both", "is_enclosed": True,
    }


def _link(stair: dict, link_id: str) -> dict:
    return {
        "id": link_id, "type": "stairs",
        "from": {"level": stair["from_level"], "position": stair["bottom"]},
        "to": {"level": stair["to_level"], "position": stair["top"]},
        "cost_multiplier": STAIR_COST, "bidirectional": True,
    }


def _walkable(spec: dict, level_id: str, doors: list[dict], t: float, r: float) -> MultiPolygon:
    free = [_room_polygon(rm["rect"], t) for rm in spec["rooms"] if rm["level"] == level_id]
    free += [door_gap_box(d["segment"], t) for d in doors if d["level"] == level_id]
    blockers = [box(*o["rect"]) for o in spec.get("obstacles", []) if o["level"] == level_id]
    blockers += [box(*s["rect"]) for s in spec.get("stairs", []) if level_id in (s["from_level"], s["to_level"])]
    return walkable_from_parts(free, blockers, r)


def _meta(spec: dict) -> dict:
    return {
        "name": spec["name"], "source": "synthetic", "source_file": f"tools.layouts:{spec['name']}",
        "units": "meters", "up_axis": "y", "scale_method": "manual", "scale_confidence": 1.0,
        "generated_by": f"tools.layouts {_git_sha()}", "generated_at": FIXED_TIME,
    }


def _doors_and_exits(spec: dict, t: float) -> tuple[list[dict], list[dict]]:
    polys = [(rm["id"], rm["level"], _room_polygon(rm["rect"], t)) for rm in spec["rooms"]]
    doors, exits = [], []
    for i, d in enumerate(spec["doors"], 1):
        on_level = [(rid, p) for rid, lvl, p in polys if lvl == d["level"]]
        doors.append(_door(d, f"door_{i:02d}", on_level, t))
        if "exit" in d:
            exits.append(_exit(d, doors[-1], f"exit_{len(exits) + 1:02d}"))
    return doors, exits


def build_scene(spec: dict) -> dict:
    """Full contract scene (dict, JSON-ready, floats rounded to 1 mm) from a layout spec."""
    t, r = spec.get("wall_thickness_m", 0.15), spec.get("agent_radius_m", 0.25)
    doors, exits = _doors_and_exits(spec, t)
    walls = []
    for level in spec["levels"]:
        rects = [rm["rect"] for rm in spec["rooms"] if rm["level"] == level["id"]]
        walls += _walls(level, rects, [d for d in spec["doors"] if d["level"] == level["id"]], t)
    stairs = [_stair(s, f"stair_{i:02d}") for i, s in enumerate(spec.get("stairs", []), 1)]
    walkable = {lv["id"]: _walkable(spec, lv["id"], doors, t, r) for lv in spec["levels"]}
    elevations = {lv["id"]: lv["elevation_m"] for lv in spec["levels"]}
    return _round({
        "schema_version": "1.0",
        "meta": _meta(spec),
        "levels": spec["levels"],
        "walls": walls,
        "rooms": [_room(rm, t) for rm in spec["rooms"]],
        "doors": doors,
        "exits": exits,
        "stairs": stairs,
        "elevators": [
            {"id": f"elev_{i:02d}", "levels": e["levels"], "position": e["position"], "usable_in_fire": e["usable_in_fire"]}
            for i, e in enumerate(spec.get("elevators", []), 1)
        ],
        "obstacles": [
            {"id": f"obs_{i:02d}", "level": o["level"], "polygon": ring(box(*o["rect"])), "height_m": o["height"],
             "kind": o["kind"], "passable": False}
            for i, o in enumerate(spec.get("obstacles", []), 1)
        ],
        "points_of_interest": [
            {"id": f"poi_{i:02d}", "level": p["level"], "position": p["position"], "kind": p["kind"]}
            for i, p in enumerate(spec.get("pois", []), 1)
        ],
        "walkable": {lvl: walkable_to_contract(mp) for lvl, mp in walkable.items()},
        "navmesh": navmesh_from_walkable(walkable, elevations, r),
        "links": [_link(s, f"link_{i:02d}") for i, s in enumerate(stairs, 1)],
        "assets": {"display_model": None, "floorplan_overlay": None, "navmesh_debug": None},
        "warnings": [],
        "extensions": {},
    })


# --- fixture specs -----------------------------------------------------------


def _level(level_id: str, name: str, elevation: float, ceiling: float = 3.0) -> dict:
    return {"id": level_id, "name": name, "elevation_m": elevation, "ceiling_height_m": ceiling}


def _rm(room_id: str, level: str, name: str, room_type: str, rect: list[float]) -> dict:
    return {"id": room_id, "level": level, "name": name, "type": room_type, "rect": rect}


def _dr(level: str, center: list[float], axis: str, width: float, exit: dict | None = None) -> dict:
    door = {"level": level, "center": center, "axis": axis, "width": width}
    return {**door, "exit": exit} if exit else door


def tiny_office() -> dict:
    """16 x 10 m single-level office: corridor along X, three offices per side, two exits."""
    lv = "L1"
    north = [[0, 0, 5, 4], [5, 0, 10, 4], [10, 0, 16, 4]]
    south = [[0, 6, 6, 10], [6, 6, 11, 10], [11, 6, 16, 10]]
    rooms = [_rm("L1_corridor", lv, "Corridor", "corridor", [0, 4, 16, 6])]
    rooms += [_rm(f"L1_office_n{i}", lv, f"Office N{i}", "office", r) for i, r in enumerate(north, 1)]
    rooms += [_rm(f"L1_office_s{i}", lv, f"Office S{i}", "office", r) for i, r in enumerate(south, 1)]
    doors = [_dr(lv, [x, 4], "x", 0.9) for x in (2.5, 7.5, 13.0)]
    doors += [_dr(lv, [x, 6], "x", 0.9) for x in (3.0, 8.5, 13.5)]
    doors += [
        _dr(lv, [0, 5], "z", 1.6, {"label": "West Exit", "leads_to": "outside", "assembly_point": [-8, 5]}),
        _dr(lv, [16, 5], "z", 1.8, {"label": "Main Entrance", "leads_to": "outside", "assembly_point": [24, 5]}),
    ]
    obstacles = [
        {"level": lv, "rect": [1.5, 1.0, 3.1, 1.8], "height": 0.75, "kind": "furniture"},
        {"level": lv, "rect": [6.5, 1.0, 8.1, 1.8], "height": 0.75, "kind": "furniture"},
        {"level": lv, "rect": [12.5, 8.0, 14.1, 8.8], "height": 0.75, "kind": "furniture"},
        {"level": lv, "rect": [0.075, 8.5, 0.6, 9.5], "height": 1.8, "kind": "furniture"},
    ]
    pois = [
        {"level": lv, "position": [8.0, 4.2], "kind": "fire_extinguisher"},
        {"level": lv, "position": [15.8, 4.3], "kind": "alarm_pull"},
        {"level": lv, "position": [0.2, 5.0], "kind": "fire_exit_sign"},
        {"level": lv, "position": [15.8, 5.0], "kind": "fire_exit_sign"},
        {"level": lv, "position": [24, 5], "kind": "assembly_point"},
    ]
    return {
        "name": "tiny_office", "wall_thickness_m": 0.15, "agent_radius_m": 0.25,
        "levels": [_level(lv, "Ground", 0.0)], "rooms": rooms, "doors": doors,
        "obstacles": obstacles, "pois": pois,
    }


def _school_level(lv: str) -> tuple[list[dict], list[dict]]:
    """Rooms and interior doors of one school floor (identical footprint on both levels)."""
    rooms = [
        _rm(f"{lv}_stair_w", lv, "Stairwell W", "stairwell", [0, 5, 6, 8]),
        _rm(f"{lv}_corridor", lv, "Corridor", "corridor", [6, 5, 22, 8]),
        _rm(f"{lv}_stair_e", lv, "Stairwell E", "stairwell", [22, 5, 28, 8]),
    ]
    for side, (z0, z1) in (("n", (0, 5)), ("s", (8, 13))):
        for i, (x0, x1) in enumerate(((0, 9), (9, 19), (19, 28)), 1):
            rooms.append(_rm(f"{lv}_class_{side}{i}", lv, f"Classroom {lv}-{side.upper()}{i}", "classroom", [x0, z0, x1, z1]))
    doors = [_dr(lv, [x, z], "x", 0.9) for z in (5, 8) for x in (7.5, 14, 20.5)]
    doors += [_dr(lv, [6, 6.5], "z", 1.2), _dr(lv, [22, 6.5], "z", 1.2)]
    return rooms, doors


def two_floor_school() -> dict:
    """28 x 13 m two-storey school; the upper floor reaches the exits only by stairs."""
    rooms, doors = [], []
    for lv in ("L1", "L2"):
        r, d = _school_level(lv)
        rooms, doors = rooms + r, doors + d
    doors += [
        _dr("L1", [0, 7.0], "z", 1.2, {"label": "West Exit", "leads_to": "outside", "assembly_point": [-10, 7]}),
        _dr("L1", [28, 7.0], "z", 1.2, {"label": "East Exit", "leads_to": "outside", "assembly_point": [38, 7]}),
    ]
    stairs = [
        {"from_level": "L1", "to_level": "L2", "rect": [1.5, 5.2, 4.0, 6.4], "bottom": [4.5, 5.8], "top": [1.0, 5.8]},
        {"from_level": "L1", "to_level": "L2", "rect": [24, 5.2, 26.5, 6.4], "bottom": [23.5, 5.8], "top": [27.0, 5.8]},
    ]
    pois = [
        {"level": lv, "position": pos, "kind": kind}
        for lv in ("L1", "L2")
        for pos, kind in (([14, 5.2], "fire_extinguisher"), ([6.2, 7.6], "alarm_pull"), ([21.8, 7.6], "alarm_pull"))
    ]
    pois += [{"level": "L1", "position": [0.2, 7.0], "kind": "fire_exit_sign"},
             {"level": "L1", "position": [27.8, 7.0], "kind": "fire_exit_sign"}]
    return {
        "name": "two_floor_school", "wall_thickness_m": 0.15, "agent_radius_m": 0.25,
        "levels": [_level("L1", "Ground floor", 0.0), _level("L2", "First floor", 3.5)],
        "rooms": rooms, "doors": doors, "stairs": stairs,
        "elevators": [{"levels": ["L1", "L2"], "position": [25.0, 7.4], "usable_in_fire": False}],
        "pois": pois,
    }


def _q(value: float) -> float:
    """Snap to a 0.1 m grid so procedural plans have tidy coordinates."""
    return round(float(value) * 10) / 10


def _row(rng: np.random.Generator, n: int, z0: float, z1: float, prefix: str) -> list[dict]:
    rooms, x = [], 0.0
    for i in range(n):
        w = _q(rng.uniform(3.0, 6.0))
        rooms.append(_rm(f"L1_office_{prefix}{i + 1}", "L1", f"Office {prefix.upper()}{i + 1}", "office", [x, z0, _q(x + w), z1]))
        x = _q(x + w)
    return rooms


def _door_on(rng: np.random.Generator, x0: float, x1: float, z: float, width: float = 0.9) -> dict:
    """Door on the horizontal wall z, at least 0.6 m clear of the corners x0 and x1."""
    margin = 0.6 + width / 2
    return _dr("L1", [_q(rng.uniform(x0 + margin, x1 - margin)), z], "x", width)


def _wing(rng: np.random.Generator, parent: dict, outward: float) -> tuple[dict, dict]:
    """Extra room on the outer side of `parent`, making the plan an asymmetric L/T."""
    x0, z0, x1, z1 = parent["rect"]
    ww, wd = _q(rng.uniform(3.0, x1 - x0)), _q(rng.uniform(3.5, 5.0))
    xs = x0 if rng.random() < 0.5 else _q(x1 - ww)
    zw = z0 if outward < 0 else z1
    rect = [xs, zw - wd, _q(xs + ww), zw] if outward < 0 else [xs, zw, _q(xs + ww), zw + wd]
    return _rm("L1_office_wing", "L1", "Office Wing", "office", rect), _door_on(rng, xs, _q(xs + ww), zw)


def _desk(rect: list[float]) -> dict:
    cx, cz = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return {"level": "L1", "rect": [cx - 0.7, cz - 0.35, cx + 0.7, cz + 0.35], "height": 0.75, "kind": "furniture"}


def corridor_office(seed: int) -> dict:
    """Procedural single-level double-loaded corridor office spec (deterministic in seed).

    4 to 10 rooms of width 3-6 m and depth 3.5-5 m along a 1.6-2.4 m corridor
    running along X, exits (1.2-1.8 m) at both corridor ends, sometimes an
    extra wing room off one office.
    """
    rng = np.random.default_rng(seed)
    n_rooms = int(rng.integers(4, 11))
    has_wing = n_rooms < 10 and rng.random() < 0.35
    n_offices = n_rooms - int(has_wing)
    n_north = (n_offices + int(rng.integers(0, 2))) // 2
    dn, cw, ds = _q(rng.uniform(3.5, 5.0)), _q(rng.uniform(1.6, 2.4)), _q(rng.uniform(3.5, 5.0))
    north = _row(rng, n_north, 0.0, dn, "n")
    south = _row(rng, n_offices - n_north, _q(dn + cw), _q(dn + cw + ds), "s")
    length = max(north[-1]["rect"][2], south[-1]["rect"][2])
    doors = [_door_on(rng, r["rect"][0], r["rect"][2], dn) for r in north]
    doors += [_door_on(rng, r["rect"][0], r["rect"][2], _q(dn + cw)) for r in south]
    offices = north + south
    if has_wing:
        k = int(rng.integers(0, len(offices)))
        wing, wing_door = _wing(rng, offices[k], -1.0 if k < len(north) else 1.0)
        offices, doors = offices + [wing], doors + [wing_door]
    exit_w, zc = min(_q(rng.uniform(1.2, 1.8)), _q(cw - 0.2)), round(dn + cw / 2, 3)
    doors += [
        _dr("L1", [0.0, zc], "z", exit_w, {"label": "West Exit", "leads_to": "outside"}),
        _dr("L1", [length, zc], "z", exit_w, {"label": "East Exit", "leads_to": "outside"}),
    ]
    return {
        "name": f"corridor_office_{seed}", "wall_thickness_m": 0.15, "agent_radius_m": 0.25,
        "levels": [_level("L1", "Ground", 0.0)],
        "rooms": [_rm("L1_corridor", "L1", "Corridor", "corridor", [0.0, dn, length, _q(dn + cw)])] + offices,
        "doors": doors, "obstacles": [_desk(r["rect"]) for r in offices],
    }


# --- negative fixtures and CLI -----------------------------------------------


def _add_island(scene: dict) -> None:
    """Isolated 2 x 2 m walkable square (plus its two triangles) outside the building."""
    sq = [[20.0, 0.0], [22.0, 0.0], [22.0, 2.0], [20.0, 2.0]]
    nav = scene["navmesh"]
    n = len(nav["vertices"])
    scene["walkable"]["L1"]["polygons"].append({"exterior": sq, "holes": []})
    nav["vertices"] += [[x, 0.0, z] for x, z in sq]
    nav["triangles"] += [[n, n + 2, n + 1], [n, n + 3, n + 2]]
    nav["triangle_level"] += ["L1", "L1"]


MUTATIONS = {
    "SCHEMA_INVALID": lambda s: s.update(schema_version="0.9"),
    "DUPLICATE_ID": lambda s: s["doors"][1].update(id=s["doors"][0]["id"]),
    "BAD_REFERENCE": lambda s: s["exits"][0].update(door_id="door_missing"),
    "INVALID_POLYGON": lambda s: s["walls"][0].update(polygon=[[0, 0], [1, 1], [1, 0], [0, 1]]),
    "EXTENT_OUT_OF_RANGE": lambda s: s["walls"].append(
        {"id": "wall_far", "level": "L1", "polygon": [[600, 0], [600.15, 0], [600.15, 1], [600, 1]], "height_m": 3.0}),
    "DOOR_TOO_NARROW": lambda s: s["doors"][0].update(width_m=0.4),
    "NO_EXITS": lambda s: s.update(exits=[]),
    "ROOM_UNREACHABLE": lambda s: s["doors"].pop(0),
    "NAVMESH_INVALID": lambda s: s["navmesh"]["triangles"][0].__setitem__(0, len(s["navmesh"]["vertices"]) + 10),
    "NAVMESH_ISLAND": _add_island,
}


def negative_fixtures(scene: dict) -> dict[str, dict]:
    """One minimal mutation of a valid single-level `scene` per validator error code."""
    out = {}
    for code, mutate in MUTATIONS.items():
        mutated = copy.deepcopy(scene)
        mutate(mutated)
        out[code] = mutated
    return out


def _write(path: Path, scene: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scene, indent=1) + "\n")
    print(f"wrote {path.relative_to(REPO)}")


def main() -> None:
    office = build_scene(tiny_office())
    _write(FIXTURES / "tiny_office.scene.json", office)
    _write(FIXTURES / "two_floor_school.scene.json", build_scene(two_floor_school()))
    for code, scene in negative_fixtures(office).items():
        _write(FIXTURES / "negative" / f"{code}.scene.json", scene)


if __name__ == "__main__":
    main()
