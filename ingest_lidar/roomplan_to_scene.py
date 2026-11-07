"""Path A: Apple RoomPlan JSON (CapturedRoom, CapturedStructure, or a folder of room
JSONs) -> scene.json + display.glb + navmesh_debug.glb + debug/.

RoomPlan's frame is the contract's frame (meters, right-handed, Y up, plan = XZ),
so x and z pass through unchanged; only y is shifted so each story's floor sits
at its level elevation. Rooms from a folder that do not fit together (separate
capture sessions) are re-aligned first (roomplan_align.py). Encoding details:
samples/roomplan/FORMAT.md; failure modes handled: research/ROOMPLAN_ROBUSTNESS.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import trimesh
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from contract.validate_scene import validate

from .display import write_navmesh_debug
from .geometry import (door_gap_box, exit_reachable_parts, extrude_xz, navmesh_from_walkable, ring,
                       walkable_from_parts, walkable_to_contract)
from .pipeline import _git_sha, _warn
from .roomplan_align import align, rigid

WALL_THICKNESS_M = 0.12
DOOR_PROBE_M = (0.3, 0.6)          # look for the room on each side at these distances
SNAP_TOL_M = 0.3                   # door -> nearest wall when its parent is missing
DOOR_DEDUPE_M = 0.6                # two room scans see the same door; merge copies this close
SILL_MAX_M = 0.15                  # openings with a higher sill are pass-throughs, not doors
DOOR_WIDTH_M = (0.6, 3.0)          # contract range (validator DOOR_TOO_NARROW)
EXIT_TAG_TOL_M = 1.5
POCKET_M = 2.0                     # a door whose far side reaches a scanned room within this is a closet/pocket
MIN_WALL_M, MIN_OPENING_M = 0.1, 0.2  # shorter surfaces are scan noise
DUP_WALL_M = 0.1                   # same-facing walls this close (and overlapping) are one wall seen twice
HANGING_M = 1.0                    # objects whose bottom is this high (wall TV, upper cabinet) do not block walking
SURFACES = ("walls", "doors", "windows", "openings", "floors")
KEYS = SURFACES + ("objects", "sections")
ROOM_TYPES = {"bathroom": "restroom", "unidentified": "unknown"}  # everything else -> office


# --- input -------------------------------------------------------------------


def _read_json(p: Path) -> object:
    try:
        return json.loads(p.read_text())
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _is_capture(d: object) -> bool:
    """A CapturedRoom/CapturedStructure dict: 'walls' is a list of surfaces with transforms."""
    return isinstance(d, dict) and isinstance(d.get("walls"), list) and \
        all(isinstance(w, dict) and "transform" in w for w in d["walls"])


def _natural(p: Path) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(p))]


def _load(input_path: Path, warnings: list[dict]) -> tuple[list[dict], dict | None, str, list[str]]:
    """(rooms, merged, format, source files). rooms: CapturedRoom dicts; merged: a structure's top-level lists."""
    if input_path.is_dir():
        st = input_path / "structure.json"
        data = _read_json(st) if st.exists() else None
        if _is_capture(data) and (data["walls"] or any(_is_capture(r) and r["walls"] for r in data.get("rooms", []))):
            return _load(st, warnings)
        if st.exists():
            warnings.append(_warn("STRUCTURE_UNUSABLE", "structure.json is empty or unreadable; using the "
                                  "per-room JSON files instead", "structure.json"))
        files = [(p, d) for p in sorted(input_path.rglob("*.json"), key=_natural) if p.name != "structure.json"
                 and _is_capture(d := _read_json(p)) and d["walls"] and "rooms" not in d]
        if not files:
            raise ValueError(f"{input_path}: no RoomPlan JSON (with a 'walls' list) found")
        return [d for _, d in files], None, "room_directory", [str(p.relative_to(input_path)) for p, _ in files]
    data = _read_json(input_path)
    if not _is_capture(data):
        raise ValueError(f"{input_path}: not a RoomPlan CapturedRoom/CapturedStructure (no 'walls' list)")
    if "rooms" in data:
        rooms = [r for r in data["rooms"] if _is_capture(r)]
        return rooms, data if data["walls"] else None, "CapturedStructure", [input_path.name]
    return [data], None, "CapturedRoom", [input_path.name]


def _finite(xs: object, n: int) -> bool:
    try:
        a = np.asarray(xs, float)
    except (TypeError, ValueError):
        return False
    return a.shape == (n,) and bool(np.all(np.isfinite(a)))


def _sane(e: object, key: str) -> bool:
    """Drop elements RoomPlan should never emit but noisy/partial exports do: bad transforms, slivers."""
    if not isinstance(e, dict):
        return False
    if key == "sections":
        return _finite(e.get("center"), 3) and isinstance(e.get("label"), str)
    if not (_finite(e.get("transform"), 16) and _finite(e.get("dimensions"), 3)):
        return False
    dims = e["dimensions"]
    if key == "walls":
        return dims[0] >= MIN_WALL_M and dims[1] > 0
    if key in ("doors", "windows", "openings"):
        return dims[0] >= MIN_OPENING_M and dims[1] > 0
    if key == "objects":
        return min(dims) > 0.01
    return True


def _floor_y_of(room: dict) -> float | None:
    ys = [_m(f["transform"])[1, 3] for f in room.get("floors", []) if _sane(f, "floors")]
    ys = ys or [_m(w["transform"])[1, 3] - w["dimensions"][1] / 2 for w in room.get("walls", []) if _sane(w, "walls")]
    return float(np.median(ys)) if ys else None


def _flatten(rooms: list[dict], merged: dict | None, warnings: list[dict]) -> dict:
    """Flat lists of sane element copies; room-file elements carry their room index and floor height."""
    if merged is not None:
        src = [(k, None, None, e) for k in KEYS for e in merged.get(k) or []]
    else:
        fy = [_floor_y_of(r) for r in rooms]
        src = [(k, i, fy[i], e) for i, r in enumerate(rooms) for k in KEYS for e in r.get(k) or []]
    flat: dict = {k: [] for k in KEYS}
    bad = 0
    for k, i, fy, e in src:
        if not _sane(e, k):
            bad += 1
            continue
        flat[k].append({**e, "_unit": i, "_fy": fy})
    if bad:
        warnings.append(_warn("DEGENERATE_SURFACE", f"skipped {bad} surfaces/objects with invalid transforms "
                              "or sizes under the scan-noise limits"))
    return flat


def _m(t: list[float]) -> np.ndarray:
    """RoomPlan transform (16 floats, column-major) -> 4x4 matrix acting on column vectors."""
    return np.asarray(t, float).reshape(4, 4).T


def _xz(v: np.ndarray) -> np.ndarray:
    return np.asarray(v, float)[[0, 2]]


def _r(v: float) -> float:
    return round(float(v), 3) + 0.0


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.hypot(*v))
    return v / n if n > 1e-9 else np.array([1.0, 0.0])


def _cat(o: dict) -> str:
    c = o.get("category")
    return next(iter(c)) if isinstance(c, dict) and c else str(c or "unknown")


def _story(e: dict) -> int:
    s = e.get("story", 0)
    return int(s) if isinstance(s, (int, float)) else 0


# --- levels ------------------------------------------------------------------


def _floor_heights(flat: dict) -> dict[int, float]:
    """Median floor y per story (floors[], else wall bottoms)."""
    ys: dict[int, list[float]] = defaultdict(list)
    floor_stories = {_story(f) for f in flat["floors"]}
    for f in flat["floors"]:
        ys[_story(f)].append(_m(f["transform"])[1, 3])
    for w in flat["walls"]:
        if _story(w) not in floor_stories:
            ys[_story(w)].append(_m(w["transform"])[1, 3] - w["dimensions"][1] / 2)
    return {s: float(np.median(v)) for s, v in ys.items()} or {0: 0.0}


# --- geometry ----------------------------------------------------------------


def _wall_line(w: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """(center xz, unit direction xz, unit inward normal xz, length) of a wall's scanned surface."""
    m = _m(w["transform"])
    return _xz(m[:3, 3]), _unit(_xz(m[:3, 0])), _unit(_xz(m[:3, 2])), float(w["dimensions"][0])


def _curve_points(w: dict) -> np.ndarray | None:
    """Arc of a curved wall in world xz, or None if absent or inconsistent with the wall's extent.

    FORMAT.md: center (2 floats), radius, start/endAngle as Measurement<UnitAngle>. Apple does not
    say which plane the center is in; we read it as the wall's local (x, z) and accept the arc only
    if its chord matches the wall's length and center. ponytail: unverified on a real curved scan.
    """
    cv = w.get("curve")
    if not isinstance(cv, dict):
        return None
    try:
        def ang(a: dict) -> float:
            conv = a["unit"]["converter"]
            return float(np.radians(a["value"] * conv["coefficient"] + conv.get("constant", 0.0)))
        cx, cz = (float(v) for v in cv["center"])
        r, a0, a1 = float(cv["radius"]), ang(cv["startAngle"]), ang(cv["endAngle"])
    except (KeyError, TypeError, ValueError):
        return None
    t = np.linspace(a0, a1, max(4, int(abs(a1 - a0) / np.radians(10)) + 2))
    local = np.c_[cx + r * np.cos(t), np.zeros_like(t), cz + r * np.sin(t), np.ones_like(t)]
    pts = (_m(w["transform"]) @ local.T).T[:, [0, 2]]
    c, _, _, length = _wall_line(w)
    chord = float(np.hypot(*(pts[-1] - pts[0])))
    if not np.all(np.isfinite(pts)) or abs(chord - length) > 0.4 * length or \
            np.hypot(*((pts[0] + pts[-1]) / 2 - c)) > 0.5:
        return None
    return pts


def _wall_path(w: dict, extend: float = 0.0) -> np.ndarray:
    """Polyline of the wall's scanned surface in xz (2 points, or an arc for curved walls)."""
    arc = _curve_points(w)
    if arc is not None:
        return arc
    c, d, _, length = _wall_line(w)
    return np.array([c - d * (length / 2 + extend), c + d * (length / 2 + extend)])


def _wall_polygon(w: dict) -> Polygon:
    """0.12 m slab whose inner face is the scanned surface (shifted away from the room, local -z)."""
    arc = _curve_points(w)
    if arc is not None:
        return LineString(arc).buffer(WALL_THICKNESS_M / 2, cap_style="flat")
    c, d, n, length = _wall_line(w)
    c = c - n * WALL_THICKNESS_M / 2
    h, t = d * length / 2, n * WALL_THICKNESS_M / 2
    return Polygon([c - h - t, c + h - t, c + h + t, c - h + t])


def _dedupe_walls(walls: list[dict]) -> list[dict]:
    """Drop a wall from another room file that repeats a kept wall (same face, same side, overlapping)."""
    kept: list[dict] = []
    for w in walls:
        c, d, n, length = _wall_line(w)
        dup = False
        for k in kept:
            c2, d2, n2, l2 = _wall_line(k)
            if k["_unit"] == w["_unit"] or _story(k) != _story(w) or n @ n2 < 0.9 or abs((c - c2) @ n2) > DUP_WALL_M:
                continue
            s = (c - c2) @ d2
            overlap = min(s + length / 2, l2 / 2) - max(s - length / 2, -l2 / 2)
            dup = overlap > 0.8 * min(length, l2)
            if dup:
                break
        if not dup:
            kept.append(w)
    return kept


def _floor_polygon(f: dict) -> Polygon | None:
    m = _m(f["transform"])
    pts = [_xz(m @ [c[0], c[1], 0.0, 1.0]) for c in f.get("polygonCorners") or [] if _finite(c, 3)]
    if len(pts) < 3:
        return None
    poly = Polygon(pts).buffer(0)
    parts = [poly] if isinstance(poly, Polygon) else list(getattr(poly, "geoms", []))
    best = max(parts, key=lambda p: p.area, default=None)
    return best if best is not None and best.area > 0.5 else None


def _polygonize_walls(walls: list[dict]) -> list[Polygon]:
    """Closed cells of the wall surfaces, each wall extended 15 cm to close corner gaps."""
    lines = [LineString(_wall_path(w, extend=0.15)) for w in walls]
    return [p for p in polygonize(unary_union(lines)) if p.area > 1.0]


def _rooms_from_walls(walls: list[dict]) -> list[Polygon]:
    """Fallback when a story has no floors: polygonize wall surfaces, else their hull."""
    polys = _polygonize_walls(walls)
    if polys:
        return polys
    hull = unary_union([LineString(_wall_path(w)) for w in walls]).convex_hull
    return [hull] if isinstance(hull, Polygon) and hull.area > 1.0 else []


def _room_outline(room: dict) -> tuple[list[Polygon], bool]:
    """One room file: its floor polygon(s), else the loop its walls close (True = from walls)."""
    polys = [p for f in room.get("floors", []) if (p := _floor_polygon(f)) is not None]
    if polys:
        u = unary_union(polys)
        return [p for p in getattr(u, "geoms", [u]) if p.area > 1.0], False
    cells = _polygonize_walls(room.get("walls", []))
    if cells:
        u = unary_union(cells)
        return [max(getattr(u, "geoms", [u]), key=lambda p: p.area)], True
    return _rooms_from_walls(room.get("walls", [])), True


def _footprint(o: dict) -> Polygon:
    m = _m(o["transform"])
    c, ax, az = _xz(m[:3, 3]), _unit(_xz(m[:3, 0])), _unit(_xz(m[:3, 2]))
    hx, hz = ax * o["dimensions"][0] / 2, az * o["dimensions"][2] / 2
    return Polygon([c - hx - hz, c + hx - hz, c + hx + hz, c - hx + hz])


# --- separate sessions ---------------------------------------------------------


def _door_features(room: dict) -> list[tuple]:
    out = []
    for s in room["doors"] + room["openings"]:
        m = _m(s["transform"])
        out.append((_xz(m[:3, 3]), _unit(_xz(m[:3, 0])), _unit(_xz(m[:3, 2])), float(s["dimensions"][0])))
    return out


def _regroup(flat: dict, n: int) -> list[dict]:
    return [{k: [e for e in flat[k] if e["_unit"] == i] for k in KEYS} for i in range(n)]


def _align_rooms(flat: dict, n: int, warnings: list[dict]) -> dict:
    """Move room files captured in other sessions next to the rest (yaw + xz, see roomplan_align)."""
    groups = _regroup(flat, n)
    if len({_story(w) for g in groups for w in g["walls"]}) > 1:
        return flat  # ponytail: multi-story folders keep their frames; align per story if one shows up
    units = []
    for g in groups:
        polys, _ = _room_outline(g)
        units.append({"poly": max(polys, key=lambda p: p.area) if polys else Polygon(),
                      "doors": _door_features(g),
                      "walls": [_wall_line(w) for w in g["walls"]]})
    if any(u["poly"].is_empty for u in units):
        return flat
    moves, notes = align(units)
    flat["_moves"] = {str(i): {"yaw_deg": _r(np.degrees(mv[0])), "t_xz": [_r(v) for v in mv[1]]}
                      for i, mv in enumerate(moves) if mv}
    for note in notes:
        warnings.append(_warn("ROOM_REALIGNED" if "re-aligned" in note else "ROOM_NOT_ALIGNED", note))
    if not any(moves):
        return flat
    mats = [rigid(*mv) if mv else None for mv in moves]
    out = {k: [] for k in KEYS}
    out["_moves"] = flat["_moves"]
    for k in KEYS:
        for e in flat[k]:
            mat = mats[e["_unit"]]
            if mat is None:
                out[k].append(e)
            elif k == "sections":
                out[k].append({**e, "center": list(mat[:3, :3] @ np.asarray(e["center"], float) + mat[:3, 3])})
            else:
                out[k].append({**e, "transform": list((mat @ _m(e["transform"])).T.ravel())})
    return out


# --- rooms, doors, exits -------------------------------------------------------


def _label(flat: dict, story: int, poly: Polygon) -> list[str]:
    """Distinct section labels inside the polygon (several = open-plan space, e.g. kitchen + living room)."""
    labels: list[str] = []
    for s in flat["sections"]:
        if _story(s) == story and poly.contains(Point(_xz(s["center"]))) and s["label"] not in labels:
            labels.append(s["label"])
    return [lb for lb in labels if lb != "unidentified"] or labels


def _build_rooms(flat: dict, rooms_in: list[dict], level_of: dict[int, str], warnings: list[dict]) -> list[dict]:
    polys: list[tuple[int, Polygon]] = []
    for i, room in enumerate(rooms_in):
        outline, from_walls = _room_outline(room)
        story = _story(next(iter(room.get("floors") or room.get("walls") or [{}])))
        polys += [(story, p) for p in outline]
        if from_walls and outline:
            warnings.append(_warn("FLOOR_FROM_WALLS", f"room file {i} has no floor polygon; its outline "
                                  "comes from the wall loop"))
    if not polys:
        polys = [(_story(f), p) for f in flat["floors"] if (p := _floor_polygon(f)) is not None]
    if not polys:
        by_story: dict[int, list[dict]] = defaultdict(list)
        for w in flat["walls"]:
            by_story[_story(w)].append(w)
        polys = [(s, p) for s, ws in sorted(by_story.items()) for p in _rooms_from_walls(ws)]
    rooms = []
    for story, poly in polys:
        rid, n = f"r_{len(rooms) + 1:03d}", len(rooms) + 1
        labels = _label(flat, story, poly)
        rooms.append({"id": rid, "level": level_of.get(story, level_of[min(level_of)]),
                      "name": f"{' + '.join(labels)} {n}" if labels else f"Room {n}",
                      "type": ROOM_TYPES.get(labels[0], "office") if labels else "unknown",
                      "polygon": ring(poly), "area_m2": _r(poly.area),
                      "centroid": [_r(poly.centroid.x), _r(poly.centroid.y)], "has_window": False,
                      "_poly": poly})
    return rooms


def _room_at(rooms: list[dict], level: str, xz: np.ndarray, exclude: str | None = None) -> str | None:
    pt = Point(xz)
    hits = [r for r in rooms if r["level"] == level and r["id"] != exclude and r["_poly"].contains(pt)]
    return min(hits, key=lambda r: r["_poly"].area)["id"] if hits else None


def _snap(s: dict, walls: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Door/window center, direction and normal (xz), snapped onto its parent (or nearest) wall."""
    m = _m(s["transform"])
    c, d, n = _xz(m[:3, 3]), _unit(_xz(m[:3, 0])), _unit(_xz(m[:3, 2]))
    parent = next((w for w in walls if w["identifier"] == s.get("parentIdentifier")), None) \
        if s.get("parentIdentifier") else None
    if parent is None or _curve_points(parent) is not None:
        near = [(abs(np.dot(c - wc, wn)), w) for w in walls for wc, wd, wn, _ in [_wall_line(w)]
                if abs(np.dot(d, wd)) > 0.9 and _curve_points(w) is None]
        dist, parent = min(near, key=lambda t: t[0], default=(np.inf, None))
        if dist > SNAP_TOL_M:
            parent = None
    if parent is not None:
        wc, wd, wn, _ = _wall_line(parent)
        c, d, n = wc + wd * np.dot(c - wc, wd), wd, wn
    return c, d, n


def _sides(rooms: list[dict], level: str, c: np.ndarray, n: np.ndarray) -> tuple[str | None, str | None, float]:
    """Rooms on the +normal and -normal side of a door, and the gap to cross (m)."""
    a = next((r for dist in DOOR_PROBE_M if (r := _room_at(rooms, level, c + n * dist))), None)
    b, gap = None, WALL_THICKNESS_M
    for dist in DOOR_PROBE_M:
        if (b := _room_at(rooms, level, c - n * dist, exclude=a)):
            poly = next(r["_poly"] for r in rooms if r["id"] == b)
            gap = max(gap, float(poly.exterior.distance(Point(c))))
            break
    return a, b, gap


def _door_class(rooms: list[dict], level: str, c: np.ndarray, n: np.ndarray, a: str | None, b: str | None,
                hull: Polygon) -> str:
    """For a door to unscanned space: 'pocket' (a scanned room is right behind it, e.g. a closet),
    'interior' (unscanned space inside the scanned footprint) or 'exterior' (leads out of it)."""
    out = -n if a else n
    if a and _room_at(rooms, level, c - n * DOOR_PROBE_M[0]) == a:
        return "pocket"  # both sides are the same room: a door inside its own floor polygon
    for dist in np.arange(0.4, POCKET_M + 1e-6, 0.2):
        hit = _room_at(rooms, level, c + out * dist)
        if hit is not None:
            return "pocket"
    return "interior" if hull.contains(Point(c + out * 1.0)) else "exterior"


def _build_doors(flat: dict, rooms: list[dict], level_of: dict[int, str], floor_y: dict[int, float],
                 warnings: list[dict]) -> tuple[list[dict], dict, dict]:
    """Contract doors (deduplicated), gap boxes of every door copy (for walkable) and door classes."""
    doors, gaps, classes = [], defaultdict(list), {}
    hulls = {lv: unary_union([r["_poly"] for r in rooms if r["level"] == lv]).convex_hull for lv in set(level_of.values())}
    for kind_key, kind in (("doors", "swing"), ("openings", "opening")):
        for s in flat[kind_key]:
            story, (w, h) = _story(s), s["dimensions"][:2]
            level = level_of[story]
            fy = s["_fy"] if s.get("_fy") is not None else floor_y[story]
            sill = _m(s["transform"])[1, 3] - h / 2 - fy
            if kind == "opening" and sill > SILL_MAX_M:
                continue
            c, d, n = _snap(s, flat["walls"])
            a, b, gap = _sides(rooms, level, c, n)
            if a is None and b is None:
                warnings.append(_warn("DOOR_DROPPED", f"{kind_key[:-1]} at [{c[0]:.2f}, {c[1]:.2f}] touches no room",
                                      s["identifier"]))
                continue
            seg = [c - d * w / 2, c + d * w / 2]
            # The box straddles the scanned surface and reaches gap + 5 cm through the wall.
            gaps[level].append((door_gap_box([list(p) for p in seg], 2 * (gap + 0.05)), h))
            pair = sorted(x for x in (a, b) if x) + ["outside"] * ((a is None) + (b is None))
            dup = next((x for x in doors if x["level"] == level
                        and abs(np.dot(_unit(np.subtract(*x["segment"])), d)) > 0.9
                        and np.hypot(*(np.mean(x["segment"], 0) - c)) < DOOR_DEDUPE_M), None)
            if dup is not None:
                seen = sorted((set(dup["connects"]) | set(pair)) - {"outside"})
                if "outside" in dup["connects"] and len(seen) == 2:
                    dup["connects"], dup["opens_toward"] = seen, None  # the other room's copy saw the far side
                    classes.pop(dup["id"], None)
                continue
            outside = "outside" in pair
            did = f"d_{len(doors) + 1:03d}"
            if outside:
                classes[did] = _door_class(rooms, level, c, n, a, b, hulls[level])
            doors.append({"id": did, "level": level,
                          "segment": [[_r(p[0]), _r(p[1])] for p in seg],
                          "width_m": _r(np.clip(w, *DOOR_WIDTH_M)), "connects": pair, "is_exit": False,
                          "kind": kind, "fire_rated": None, "opens_toward": "outside" if outside else None})
    return doors, gaps, classes


def _components(doors: list[dict], rooms: list[dict]) -> dict[str, int]:
    """Room id -> connected component over interior doors."""
    parent = {r["id"]: r["id"] for r in rooms}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x
    for d in doors:
        a, b = d["connects"]
        if "outside" not in (a, b):
            parent[find(a)] = find(b)
    return {r["id"]: find(r["id"]) for r in rooms}


def _auto_exits(doors: list[dict], rooms: list[dict], classes: dict, unscanned_exits: bool,
                warnings: list[dict]) -> list[dict]:
    """Doors to unscanned space, best class first, so that every connected group of rooms keeps an exit.

    Order: exterior > interior > pocket. Pockets (closets) are used only when a group has nothing
    better. With unscanned_exits=False, interior doors are dropped too once the group has an exterior door.
    """
    comp = _components(doors, rooms)
    rank = {"exterior": 0, "interior": 0 if unscanned_exits else 1, "pocket": 2}
    chosen, skipped = [], defaultdict(int)
    for group in sorted(set(comp.values())):
        cand = [d for d in doors if d["id"] in classes and comp[next(c for c in d["connects"] if c != "outside")] == group]
        if not cand:
            continue
        best = min(rank[classes[d["id"]]] for d in cand)
        chosen += [d for d in cand if rank[classes[d["id"]]] == best]
        for d in cand:
            if rank[classes[d["id"]]] != best:
                skipped[classes[d["id"]]] += 1
    if skipped.get("pocket"):
        warnings.append(_warn("EXIT_POCKET_SKIPPED", f"{skipped['pocket']} door(s) open onto small unscanned "
                              "pockets (closets, gaps between scanned rooms) and are not exits"))
    if skipped.get("interior"):
        warnings.append(_warn("UNSCANNED_DOOR_NOT_EXIT", f"{skipped['interior']} door(s) to unscanned space inside "
                              "the building are not exits because an exterior door exists (unscanned_exits=False); "
                              "check that nobody must leave through them"))
    return sorted(chosen, key=lambda d: d["id"])


def _build_exits(doors: list[dict], rooms: list[dict], tags: list, classes: dict, unscanned_exits: bool,
                 warnings: list[dict]) -> list[dict]:
    """Exits: tagged doors if exit_tags.json exists, else doors to unscanned space (see _auto_exits),
    else one assumed exit."""
    def center(d: dict) -> np.ndarray:
        return np.mean(d["segment"], axis=0)

    chosen: list[tuple[dict, str]] = []
    if tags:
        for x, z, label in tags:
            best = min(doors, key=lambda d: np.hypot(*(center(d) - [x, z])), default=None)
            if best is None or np.hypot(*(center(best) - [x, z])) > EXIT_TAG_TOL_M:
                warnings.append(_warn("EXIT_TAG_UNMATCHED", f"no door within {EXIT_TAG_TOL_M} m of tag {label!r} "
                                      f"at [{x}, {z}]"))
            elif best not in [c[0] for c in chosen]:
                chosen.append((best, str(label)))
    else:
        chosen = [(d, "") for d in _auto_exits(doors, rooms, classes, unscanned_exits, warnings)]
    if not chosen and doors:
        pts = np.array([p for r in rooms for p in r["polygon"]])
        lo, hi = pts.min(0), pts.max(0)
        best = min(doors, key=lambda d: float(np.min(np.r_[center(d) - lo, hi - center(d)])))
        chosen = [(best, "")]
        warnings.append(_warn("EXIT_ASSUMED", "no door leads outside; the door nearest the scan's edge is "
                              "marked as the exit. Tag real exits in exit_tags.json.", best["id"]))
    exits = []
    for door, label in chosen:
        door["is_exit"] = True
        exits.append({"id": f"e_{len(exits) + 1:03d}", "level": door["level"], "door_id": door["id"],
                      "position": [_r(v) for v in center(door)], "width_m": door["width_m"],
                      "leads_to": "outside", "label": label or f"Exit {len(exits) + 1}", "assembly_point": None})
    return exits


# --- objects ------------------------------------------------------------------


def _build_objects(flat: dict, level_of: dict[int, str], floor_y: dict[int, float], single_level: bool,
                   warnings: list[dict]) -> tuple[list[dict], list[dict]]:
    obstacles, stairs = [], []
    for o in flat["objects"]:
        cat, level = _cat(o), level_of[_story(o)]
        poly = _footprint(o)
        m = _m(o["transform"])
        if cat == "stairs":
            c, az = _xz(m[:3, 3]), _unit(_xz(m[:3, 2]))
            sid = f"s_{len(stairs) + 1:03d}"
            # ponytail: assumes the flight rises toward local +z; RoomPlan does not say. Check on a real scan.
            stairs.append({"id": sid, "from_level": level, "to_level": level, "polygon": ring(poly),
                           "bottom": [_r(v) for v in c - az * o["dimensions"][2] / 2],
                           "top": [_r(v) for v in c + az * o["dimensions"][2] / 2],
                           "width_m": _r(max(o["dimensions"][0], 0.3)), "direction": "up", "is_enclosed": False})
            if single_level:
                warnings.append(_warn("ORPHAN_STAIR", "stairs lead to a level that was not scanned", sid))
            continue
        fy = o["_fy"] if o.get("_fy") is not None else floor_y[_story(o)]
        bottom = m[1, 3] - o["dimensions"][1] / 2 - fy
        obstacles.append({"id": f"o_{len(obstacles) + 1:03d}", "level": level, "polygon": ring(poly),
                          "height_m": _r(o["dimensions"][1]),
                          "kind": "equipment" if cat in {"refrigerator", "stove", "oven", "dishwasher",
                                                         "washerDryer", "television"} else "furniture",
                          "passable": bool(bottom > HANGING_M)})
    return obstacles, stairs


# --- outputs ------------------------------------------------------------------


def _wall_meshes(poly: Polygon, y0: float, height: float, cuts: list[tuple[Polygon, float]]) -> list[trimesh.Trimesh]:
    """Wall slab with door openings cut out: full height outside the doors, a lintel above each door."""
    solid = poly.difference(unary_union([g for g, _ in cuts])) if cuts else poly
    meshes = [extrude_xz(p, y0, height) for p in getattr(solid, "geoms", [solid]) if isinstance(p, Polygon) and p.area > 1e-4]
    for g, top in cuts:
        part = poly.intersection(g)
        if top < height - 0.01:
            meshes += [extrude_xz(p, y0 + top, height - top) for p in getattr(part, "geoms", [part])
                       if isinstance(p, Polygon) and p.area > 1e-4]
    return meshes


def _display(path: Path, scene: dict, flat: dict, floor_y: dict[int, float], gaps: dict,
             level_of: dict[int, str]) -> None:
    """Parametric model: node groups "walls_clean" (0.12 m slabs with door cut-outs), "objects", "floors"."""
    tscene = trimesh.Scene()
    for group in ("walls_clean", "objects", "floors"):
        tscene.graph.update(frame_to=group, frame_from=tscene.graph.base_frame)
    elev = {lv["id"]: lv["elevation_m"] for lv in scene["levels"]}
    for w in scene["walls"]:
        poly = Polygon(w["polygon"])
        cuts = [(g, top) for g, top in gaps[w["level"]] if g.intersects(poly)]
        meshes = _wall_meshes(poly, elev[w["level"]], w["height_m"], cuts)
        if not meshes:
            continue
        mesh = trimesh.util.concatenate(meshes)
        mesh.visual.face_colors = [235, 235, 235, 255]
        tscene.add_geometry(mesh, node_name=w["id"], geom_name=w["id"], parent_node_name="walls_clean")
    for i, o in enumerate(flat["objects"]):
        m = _m(o["transform"])
        fy = o["_fy"] if o.get("_fy") is not None else floor_y[_story(o)]
        m[1, 3] += elev[level_of[_story(o)]] - fy
        box = trimesh.creation.box(extents=o["dimensions"], transform=m)
        box.visual.face_colors = [240, 150, 60, 255]
        name = f"obj_{i:03d}_{_cat(o)}"
        tscene.add_geometry(box, node_name=name, geom_name=name, parent_node_name="objects")
    for r in scene["rooms"]:
        slab = extrude_xz(Polygon(r["polygon"]), elev[r["level"]] - 0.02, 0.02)
        slab.visual.face_colors = [200, 215, 230, 255]
        tscene.add_geometry(slab, node_name=f"floor_{r['id']}", geom_name=f"floor_{r['id']}", parent_node_name="floors")
    tscene.export(path)


def _overview(path: Path, scene: dict, px_per_m: int = 50) -> None:
    """Top view: +x right, +z down (looking down -Y, unmirrored)."""
    pts = np.array([p for c in ("walls", "rooms") for item in scene[c] for p in item["polygon"]], float)
    lo = pts.min(0) - 0.5
    size = np.ceil((pts.max(0) + 0.5 - lo) * px_per_m).astype(int)
    img = np.full((size[1], size[0], 3), 255, np.uint8)

    def poly(points: list, color: tuple, fill: bool = False) -> None:
        arr = np.round((np.asarray(points) - lo) * px_per_m).astype(np.int32)
        (cv2.fillPoly if fill else lambda i, p, c: cv2.polylines(i, p, True, c, 1))(img, [arr], color)

    for r in scene["rooms"]:
        poly(r["polygon"], (235, 225, 215), fill=True)
    for wl in scene["walkable"].values():
        for wp in wl["polygons"]:
            poly(wp["exterior"], (150, 215, 150), fill=True)
            for h in wp["holes"]:
                poly(h, (235, 225, 215), fill=True)
    for w in scene["walls"]:
        poly(w["polygon"], (40, 40, 40), fill=True)
    for o in scene["obstacles"]:
        poly(o["polygon"], (160, 160, 160) if o["passable"] else (0, 140, 255))
    for s in scene["stairs"]:
        poly(s["polygon"], (160, 0, 160))
    for d in scene["doors"]:
        a, b = (tuple(np.round((np.asarray(p) - lo) * px_per_m).astype(int).tolist()) for p in d["segment"])
        cv2.line(img, a, b, (0, 0, 230) if d["is_exit"] else (230, 80, 0), 3)
    for r in scene["rooms"]:
        xy = tuple(np.round((np.asarray(r["centroid"]) - lo) * px_per_m).astype(int).tolist())
        cv2.putText(img, f"{r['id']} {r['name']}", xy, cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 0), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def _exit_tags(input_path: Path, exit_tags: Path | None) -> list:
    tag_path = Path(exit_tags) if exit_tags else (input_path if input_path.is_dir() else input_path.parent) / "exit_tags.json"
    return json.loads(tag_path.read_text()) if tag_path.exists() else []


def convert(input_path: Path, out_dir: Path, no_llm: bool = True, exit_tags: Path | None = None,
            unscanned_exits: bool = True) -> dict:
    """Convert RoomPlan JSON (file or folder) to a validated scene; writes outputs to out_dir.

    exit_tags: exit_tags.json ([[x, z, label]]); default: exit_tags.json next to the input.
    unscanned_exits: False = doors to unscanned space inside the building are not exits once an
    exterior door exists (warned as UNSCANNED_DOOR_NOT_EXIT).
    """
    input_path, out_dir = Path(input_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[dict] = []
    rooms_in, merged, fmt, sources = _load(input_path, warnings)
    flat = _flatten(rooms_in, merged, warnings)
    if not flat["walls"]:
        raise ValueError(f"{input_path}: RoomPlan input has no walls")
    if merged is None and len(rooms_in) > 1:
        flat = _align_rooms(flat, len(rooms_in), warnings)
        n_walls = len(flat["walls"])
        flat["walls"] = _dedupe_walls(flat["walls"])
        if len(flat["walls"]) < n_walls:
            warnings.append(_warn("DUPLICATE_WALLS", f"merged {n_walls - len(flat['walls'])} walls that two room "
                                  "scans both captured"))
    unit_rooms = _regroup(flat, len(rooms_in)) if merged is None else rooms_in
    floor_y = _floor_heights(flat)
    stories = sorted(floor_y)
    level_of = {s: f"L{i + 1}" for i, s in enumerate(stories)}
    floor_y.update({_story(e): floor_y[stories[0]] for k in SURFACES + ("objects",)
                    for e in flat[k] if _story(e) not in floor_y})
    level_of.update({s: level_of[stories[0]] for s in floor_y if s not in level_of})

    walls = [{"id": f"w_{i + 1:03d}", "level": level_of[_story(w)], "polygon": ring(_wall_polygon(w)),
              "height_m": _r(max(w["dimensions"][1], 0.1))} for i, w in enumerate(flat["walls"])]
    rooms = _build_rooms(flat, unit_rooms, level_of, warnings)
    if not rooms:
        raise ValueError(f"{input_path}: no room outline (no floor polygons and the walls close no loop)")
    for s in flat["windows"]:
        c, _, n = _snap(s, flat["walls"])
        for rid in _sides(rooms, level_of[_story(s)], c, n)[:2]:
            for r in rooms:
                r["has_window"] |= r["id"] == rid
    doors, gaps, classes = _build_doors(flat, rooms, level_of, floor_y, warnings)
    exits = _build_exits(doors, rooms, _exit_tags(input_path, exit_tags), classes, unscanned_exits, warnings)
    obstacles, stairs = _build_objects(flat, level_of, floor_y, len(stories) == 1, warnings)

    walkable, levels = {}, []
    for story in stories:
        lv = level_of[story]
        boxes = [g for g, _ in gaps[lv]]
        free = [r["_poly"] for r in rooms if r["level"] == lv] + boxes
        wall_block = unary_union([Polygon(w["polygon"]) for w in walls if w["level"] == lv])
        blockers = [wall_block.difference(unary_union(boxes))] if boxes else [wall_block]
        blockers += [Polygon(o["polygon"]) for o in obstacles if o["level"] == lv and not o["passable"]]
        blockers += [Polygon(s["polygon"]) for s in stairs if s["from_level"] == lv]
        walk = walkable_from_parts(free, blockers)
        exit_pts = [e["position"] for e in exits if e["level"] == lv]
        kept = exit_reachable_parts(walk, exit_pts) if exit_pts else walk
        if kept.area < walk.area - 0.05:
            warnings.append(_warn("ROOM_UNREACHABLE", f"dropped {walk.area - kept.area:.2f} m^2 of walkable area "
                                  "that cannot reach an exit", lv))
        walkable[lv] = kept
        heights = [w["dimensions"][1] for w in flat["walls"] if _story(w) == story]
        levels.append({"id": lv, "name": f"Story {story}", "elevation_m": _r(floor_y[story] - floor_y[stories[0]]),
                       "ceiling_height_m": _r(np.median(heights) if heights else 2.5)})
    elev = {lv["id"]: lv["elevation_m"] for lv in levels}
    navmesh = navmesh_from_walkable(walkable, elev)

    counts = {k: len(flat[k]) for k in KEYS}
    scene = {
        "schema_version": "1.0",
        "meta": {"name": input_path.stem, "source": "lidar", "source_file": input_path.name, "units": "meters",
                 "up_axis": "y", "scale_method": "arkit_metric", "scale_confidence": 0.95,
                 "generated_by": f"ingest_lidar.roomplan_to_scene {_git_sha()}",
                 "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "levels": levels,
        "walls": walls, "rooms": [{k: v for k, v in r.items() if k != "_poly"} for r in rooms],
        "doors": doors, "exits": exits, "stairs": stairs, "elevators": [], "obstacles": obstacles,
        "points_of_interest": [],
        "walkable": {lv: walkable_to_contract(mp) for lv, mp in walkable.items()}, "navmesh": navmesh, "links": [],
        "assets": {"display_model": "display.glb", "floorplan_overlay": "debug/overview.png",
                   "navmesh_debug": "navmesh_debug.glb"},
        "warnings": warnings,
        "extensions": {
            "roomplan": {"format": fmt, "source_files": sources, "counts": counts,
                         "floor_y_by_story": {str(s): _r(y) for s, y in floor_y.items()},
                         "input_to_scene": "x, z unchanged (unless a room was re-aligned, see warnings); "
                                           "y minus floor_y of the lowest story",
                         "doors_merged": len(flat["doors"]) + len(flat["openings"]) - len(doors),
                         "unscanned_door_class": classes, "realigned_rooms": flat.get("_moves", {})},
            "lidar": {}},
    }
    if not no_llm:
        from .semantics import label_rooms

        scene = label_rooms(scene)
    scene["extensions"]["lidar"]["validation"] = validate(scene)
    (out_dir / "scene.json").write_text(json.dumps(scene, indent=1))
    (out_dir / "validation.json").write_text(json.dumps(scene["extensions"]["lidar"]["validation"], indent=1))
    _overview(out_dir / "debug" / "overview.png", scene)
    _display(out_dir / "display.glb", scene, flat, floor_y, gaps, level_of)
    write_navmesh_debug(out_dir / "navmesh_debug.glb", navmesh, doors)
    return scene


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ingest_lidar.roomplan_to_scene", description="RoomPlan JSON -> scene.json")
    ap.add_argument("input", type=Path, help="structure.json, a CapturedRoom JSON, or a folder of room_N.json")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--exit-tags", type=Path, help="exit_tags.json: [[x, z, label], ...] in scene frame")
    ap.add_argument("--unscanned-not-exits", action="store_true",
                    help="doors to unscanned space inside the building are not exits once an exterior door exists")
    a = ap.parse_args(argv)
    try:
        scene = convert(a.input, a.out, exit_tags=a.exit_tags, unscanned_exits=not a.unscanned_not_exits)
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    errors = [i for i in scene["extensions"]["lidar"]["validation"] if i["severity"] == "error"]
    print(f"wrote {a.out / 'scene.json'}: {len(scene['rooms'])} rooms, {len(scene['doors'])} doors, "
          f"{len(scene['exits'])} exits, validator errors: {len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
