"""Path A: Apple RoomPlan JSON (CapturedRoom, CapturedStructure, or a folder of room
JSONs from one session) -> scene.json + display.glb + navmesh_debug.glb + debug/.

RoomPlan's frame is the contract's frame (meters, right-handed, Y up, plan = XZ),
so x and z pass through unchanged; only y is shifted so each story's floor sits
at its level elevation. Encoding details: samples/roomplan/FORMAT.md.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import trimesh
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import polygonize, unary_union

from contract.validate_scene import validate

from .display import write_navmesh_debug
from .geometry import (door_gap_box, exit_reachable_parts, extrude_xz, navmesh_from_walkable, ring,
                       walkable_from_parts, walkable_to_contract)
from .pipeline import _git_sha, _warn

WALL_THICKNESS_M = 0.12
DOOR_PROBE_M = (0.3, 0.6)          # look for the room on each side at these distances
SNAP_TOL_M = 0.3                   # door -> nearest wall when its parent is missing
DOOR_DEDUPE_M = 0.6                # two room scans see the same door; merge copies this close
SILL_MAX_M = 0.15                  # openings with a higher sill are pass-throughs, not doors
DOOR_WIDTH_M = (0.6, 3.0)          # contract range (validator DOOR_TOO_NARROW)
EXIT_TAG_TOL_M = 1.5
SURFACES = ("walls", "doors", "windows", "openings", "floors")
ROOM_TYPES = {"bathroom": "restroom", "unidentified": "unknown"}  # everything else -> office


# --- input -------------------------------------------------------------------


def _load(input_path: Path) -> tuple[dict, str, list[str]]:
    """Flat {walls, doors, windows, openings, floors, objects, sections} lists, the format, source files."""
    if input_path.is_dir():
        if (input_path / "structure.json").exists():
            return _load(input_path / "structure.json")
        files = sorted(p for p in input_path.rglob("*.json") if "walls" in json.loads(p.read_text()))
        if not files:
            raise ValueError(f"{input_path}: no RoomPlan JSON (with a 'walls' key) found")
        rooms = [json.loads(p.read_text()) for p in files]
        return _concat(rooms), "room_directory", [str(p.relative_to(input_path)) for p in files]
    data = json.loads(input_path.read_text())
    if not isinstance(data, dict) or "walls" not in data:
        raise ValueError(f"{input_path}: not a RoomPlan CapturedRoom/CapturedStructure (no 'walls' key)")
    # A structure carries merged top-level lists; its rooms[] only fill in when those are empty.
    if "rooms" in data and not data["walls"] and data["rooms"]:
        return _concat(data["rooms"]), "CapturedStructure", [input_path.name]
    flat = {k: list(data.get(k, [])) for k in SURFACES + ("objects", "sections")}
    return flat, "CapturedStructure" if "rooms" in data else "CapturedRoom", [input_path.name]


def _concat(rooms: list[dict]) -> dict:
    return {k: [e for r in rooms for e in r.get(k, [])] for k in SURFACES + ("objects", "sections")}


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


# --- levels ------------------------------------------------------------------


def _floor_heights(flat: dict) -> dict[int, float]:
    """Median floor y per story (floors[], else wall bottoms)."""
    ys: dict[int, list[float]] = defaultdict(list)
    for f in flat["floors"]:
        ys[int(f.get("story", 0))].append(_m(f["transform"])[1, 3])
    for w in flat["walls"]:
        s = int(w.get("story", 0))
        if s not in {int(f.get("story", 0)) for f in flat["floors"]}:
            ys[s].append(_m(w["transform"])[1, 3] - w["dimensions"][1] / 2)
    return {s: float(np.median(v)) for s, v in ys.items()} or {0: 0.0}


# --- geometry ----------------------------------------------------------------


def _wall_line(w: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """(center xz, unit direction xz, unit inward normal xz, length) of a wall's scanned surface."""
    m = _m(w["transform"])
    return _xz(m[:3, 3]), _unit(_xz(m[:3, 0])), _unit(_xz(m[:3, 2])), float(w["dimensions"][0])


def _wall_polygon(w: dict) -> Polygon:
    """0.12 m slab whose inner face is the scanned surface (shifted away from the room, local -z)."""
    c, d, n, length = _wall_line(w)
    c = c - n * WALL_THICKNESS_M / 2
    h, t = d * length / 2, n * WALL_THICKNESS_M / 2
    return Polygon([c - h - t, c + h - t, c + h + t, c - h + t])


def _floor_polygon(f: dict) -> Polygon | None:
    m = _m(f["transform"])
    pts = [_xz(m @ [c[0], c[1], 0.0, 1.0]) for c in f.get("polygonCorners") or []]
    if len(pts) < 3:
        return None
    poly = Polygon(pts).buffer(0)
    parts = [poly] if isinstance(poly, Polygon) else list(getattr(poly, "geoms", []))
    best = max(parts, key=lambda p: p.area, default=None)
    return best if best is not None and best.area > 0.5 else None


def _rooms_from_walls(walls: list[dict]) -> list[Polygon]:
    """Fallback when there are no floors: polygonize wall surfaces (extended 5 cm), else their hull."""
    lines = []
    for w in walls:
        c, d, _, length = _wall_line(w)
        lines.append(LineString([c - d * (length / 2 + 0.05), c + d * (length / 2 + 0.05)]))
    polys = [p for p in polygonize(unary_union(lines)) if p.area > 1.0]
    if polys:
        return polys
    hull = unary_union(lines).convex_hull
    return [hull] if isinstance(hull, Polygon) and hull.area > 1.0 else []


def _footprint(o: dict) -> Polygon:
    m = _m(o["transform"])
    c, ax, az = _xz(m[:3, 3]), _unit(_xz(m[:3, 0])), _unit(_xz(m[:3, 2]))
    hx, hz = ax * o["dimensions"][0] / 2, az * o["dimensions"][2] / 2
    return Polygon([c - hx - hz, c + hx - hz, c + hx + hz, c - hx + hz])


# --- rooms, doors, exits -------------------------------------------------------


def _build_rooms(flat: dict, level_of: dict[int, str]) -> list[dict]:
    polys = [(int(f.get("story", 0)), p) for f in flat["floors"] if (p := _floor_polygon(f)) is not None]
    if not polys:
        by_story: dict[int, list[dict]] = defaultdict(list)
        for w in flat["walls"]:
            by_story[int(w.get("story", 0))].append(w)
        polys = [(s, p) for s, ws in sorted(by_story.items()) for p in _rooms_from_walls(ws)]
    rooms = []
    for story, poly in polys:
        rid = f"r_{len(rooms) + 1:03d}"
        label = next((s["label"] for s in flat["sections"] if int(s.get("story", 0)) == story
                      and poly.contains(Point(_xz(s["center"])))), None)
        rooms.append({"id": rid, "level": level_of[story],
                      "name": f"{label} {len(rooms) + 1}" if label else f"Room {len(rooms) + 1}",
                      "type": ROOM_TYPES.get(label, "office") if label else "unknown",
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
    parent = next((w for w in walls if w["identifier"] == s.get("parentIdentifier")), None)
    if parent is None:
        near = [(abs(np.dot(c - wc, wn)), w) for w in walls for wc, _, wn, _ in [_wall_line(w)]
                if abs(np.dot(d, _wall_line(w)[1])) > 0.9]
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


def _build_doors(flat: dict, rooms: list[dict], level_of: dict[int, str], floor_y: dict[int, float],
                 warnings: list[dict]) -> tuple[list[dict], list[Polygon]]:
    """Contract doors (deduplicated) and the gap boxes of every door copy (for walkable)."""
    doors, gaps = [], defaultdict(list)
    for kind_key, kind in (("doors", "swing"), ("openings", "opening")):
        for s in flat[kind_key]:
            story, (w, h) = int(s.get("story", 0)), s["dimensions"][:2]
            level = level_of[story]
            sill = _m(s["transform"])[1, 3] - h / 2 - floor_y[story]
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
            gaps[level].append(door_gap_box([list(p) for p in seg], 2 * (gap + 0.05)))
            pair = sorted(x for x in (a, b) if x) + ["outside"] * ((a is None) + (b is None))
            dup = next((x for x in doors if x["level"] == level and sorted(x["connects"]) == sorted(pair)
                        and np.hypot(*(np.mean(x["segment"], 0) - c)) < DOOR_DEDUPE_M), None)
            if dup is not None:
                continue
            outside = "outside" in pair
            doors.append({"id": f"d_{len(doors) + 1:03d}", "level": level,
                          "segment": [[_r(p[0]), _r(p[1])] for p in seg],
                          "width_m": _r(np.clip(w, *DOOR_WIDTH_M)), "connects": pair, "is_exit": False,
                          "kind": kind, "fire_rated": None, "opens_toward": "outside" if outside else None})
    return doors, gaps


def _build_exits(doors: list[dict], rooms: list[dict], tags: list, warnings: list[dict]) -> list[dict]:
    """Exits: tagged doors if exit_tags.json exists, else doors to "outside", else one assumed exit."""
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
        chosen = [(d, "") for d in doors if "outside" in d["connects"]]
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


def _build_objects(flat: dict, rooms: list[dict], level_of: dict[int, str], single_level: bool,
                   warnings: list[dict]) -> tuple[list[dict], list[dict]]:
    obstacles, stairs = [], []
    for o in flat["objects"]:
        cat, level = next(iter(o["category"])), level_of[int(o.get("story", 0))]
        poly = _footprint(o)
        if cat == "stairs":
            m = _m(o["transform"])
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
        obstacles.append({"id": f"o_{len(obstacles) + 1:03d}", "level": level, "polygon": ring(poly),
                          "height_m": _r(o["dimensions"][1]),
                          "kind": "equipment" if cat in {"refrigerator", "stove", "oven", "dishwasher",
                                                         "washerDryer", "television"} else "furniture",
                          "passable": False})
    return obstacles, stairs


# --- outputs ------------------------------------------------------------------


def _display(path: Path, scene: dict, flat: dict, floor_y: dict[int, float]) -> None:
    """Parametric model: node groups "walls_clean" (0.12 m slabs), "objects" (boxes), "floors"."""
    tscene = trimesh.Scene()
    for group in ("walls_clean", "objects", "floors"):
        tscene.graph.update(frame_to=group, frame_from=tscene.graph.base_frame)
    elev = {lv["id"]: lv["elevation_m"] for lv in scene["levels"]}
    for w in scene["walls"]:
        mesh = extrude_xz(Polygon(w["polygon"]), elev[w["level"]], w["height_m"])
        mesh.visual.face_colors = [235, 235, 235, 255]
        tscene.add_geometry(mesh, node_name=w["id"], geom_name=w["id"], parent_node_name="walls_clean")
    for i, o in enumerate(flat["objects"]):
        m = _m(o["transform"])
        m[1, 3] -= floor_y[int(o.get("story", 0))]
        box = trimesh.creation.box(extents=o["dimensions"], transform=m)
        box.visual.face_colors = [240, 150, 60, 255]
        name = f"obj_{i:03d}_{next(iter(o['category']))}"
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
        poly(o["polygon"], (0, 140, 255))
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


def _exit_tags(input_path: Path) -> list:
    tag_path = (input_path if input_path.is_dir() else input_path.parent) / "exit_tags.json"
    return json.loads(tag_path.read_text()) if tag_path.exists() else []


def convert(input_path: Path, out_dir: Path, no_llm: bool = True) -> dict:
    """Convert RoomPlan JSON (file or folder) to a validated scene; writes outputs to out_dir."""
    input_path, out_dir = Path(input_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    flat, fmt, sources = _load(input_path)
    if not flat["walls"]:
        raise ValueError(f"{input_path}: RoomPlan input has no walls")
    floor_y = _floor_heights(flat)
    stories = sorted(floor_y)
    level_of = {s: f"L{i + 1}" for i, s in enumerate(stories)}
    floor_y.update({int(e.get("story", 0)): floor_y[stories[0]] for k in SURFACES + ("objects",)
                    for e in flat[k] if int(e.get("story", 0)) not in floor_y})
    level_of.update({s: level_of[stories[0]] for s in floor_y if s not in level_of})
    warnings: list[dict] = []

    walls = [{"id": f"w_{i + 1:03d}", "level": level_of[int(w.get("story", 0))], "polygon": ring(_wall_polygon(w)),
              "height_m": _r(max(w["dimensions"][1], 0.1))} for i, w in enumerate(flat["walls"])]
    rooms = _build_rooms(flat, level_of)
    for s in flat["windows"]:
        c, _, n = _snap(s, flat["walls"])
        for rid in _sides(rooms, level_of[int(s.get("story", 0))], c, n)[:2]:
            for r in rooms:
                r["has_window"] |= r["id"] == rid
    doors, gaps = _build_doors(flat, rooms, level_of, floor_y, warnings)
    exits = _build_exits(doors, rooms, _exit_tags(input_path), warnings)
    obstacles, stairs = _build_objects(flat, rooms, level_of, len(stories) == 1, warnings)

    walkable, levels = {}, []
    for story in stories:
        lv = level_of[story]
        free = [r["_poly"] for r in rooms if r["level"] == lv] + gaps[lv]
        wall_block = unary_union([Polygon(w["polygon"]) for w in walls if w["level"] == lv])
        blockers = [wall_block.difference(unary_union(gaps[lv]))] if gaps[lv] else [wall_block]
        blockers += [Polygon(o["polygon"]) for o in obstacles if o["level"] == lv]
        blockers += [Polygon(s["polygon"]) for s in stairs if s["from_level"] == lv]
        walk = walkable_from_parts(free, blockers)
        exit_pts = [e["position"] for e in exits if e["level"] == lv]
        kept = exit_reachable_parts(walk, exit_pts) if exit_pts else walk
        if kept.area < walk.area - 0.05:
            warnings.append(_warn("ROOM_UNREACHABLE", f"dropped {walk.area - kept.area:.2f} m^2 of walkable area "
                                  "that cannot reach an exit", lv))
        walkable[lv] = kept
        heights = [w["dimensions"][1] for w in flat["walls"] if int(w.get("story", 0)) == story]
        levels.append({"id": lv, "name": f"Story {story}", "elevation_m": _r(floor_y[story] - floor_y[stories[0]]),
                       "ceiling_height_m": _r(np.median(heights) if heights else 2.5)})
    elev = {lv["id"]: lv["elevation_m"] for lv in levels}
    navmesh = navmesh_from_walkable(walkable, elev)

    counts = {k: len(flat[k]) for k in SURFACES + ("objects", "sections")}
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
                         "input_to_scene": "x, z unchanged; y minus floor_y of the lowest story",
                         "doors_merged": len(flat["doors"]) + len(flat["openings"]) - len(doors)},
            "lidar": {}},
    }
    if not no_llm:
        from .semantics import label_rooms

        scene = label_rooms(scene)
    scene["extensions"]["lidar"]["validation"] = validate(scene)
    (out_dir / "scene.json").write_text(json.dumps(scene, indent=1))
    (out_dir / "validation.json").write_text(json.dumps(scene["extensions"]["lidar"]["validation"], indent=1))
    _overview(out_dir / "debug" / "overview.png", scene)
    _display(out_dir / "display.glb", scene, flat, floor_y)
    write_navmesh_debug(out_dir / "navmesh_debug.glb", navmesh, doors)
    return scene
