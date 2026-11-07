"""End-to-end Path B: scan file -> scene.json + display.glb + debug artifacts.

All outputs are in the scene frame (meters, Y up, 2D points [x, z]). The
input->scene transform is stored in extensions.lidar.input_to_scene.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Point, Polygon

from contract.validate_scene import validate

from . import grid as G
from .display import write_display, write_navmesh_debug
from .geometry import exit_reachable_parts, navmesh_from_walkable, ring, walkable_to_contract
from .load import Scan, load_scan
from .orient import orient

LEVEL = "L1"


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, timeout=5).stdout.strip() or "nogit"
    except (OSError, subprocess.SubprocessError):
        return "nogit"


def _warn(code: str, msg: str, ref: str | None = None, sev: str = "warn") -> dict:
    return {"code": code, "message": msg, "ref": ref, "severity": sev}


def _m1_rooms(g: G.Grid, m: G.Masks) -> tuple[list[dict], list[dict]]:
    """Fallback room model: one room per free-space component >= 1 m^2, no interior doors."""
    n, lab, st = G.components(m.free)
    rooms = []
    for i in range(1, n):
        if st[i, 4] * g.res**2 < 1.0:
            continue
        mp = G.vectorize(lab == i, g)
        if mp.is_empty:
            continue
        poly = Polygon(max(mp.geoms, key=lambda p: p.area).exterior)
        rooms.append({"id": f"r_{len(rooms) + 1:03d}", "level": LEVEL, "name": f"Space {len(rooms) + 1}",
                      "type": "unknown", "polygon": ring(poly), "area_m2": round(poly.area, 3),
                      "centroid": [round(poly.centroid.x, 3), round(poly.centroid.y, 3)], "has_window": False})
    return rooms, []


def _segment(g: G.Grid, m: G.Masks, ceiling: float) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Walls, rooms, interior doors, obstacles. Uses M2 modules when present."""
    try:
        from .obstacles import detect_obstacles
        from .rooms import segment_rooms
        from .walls import detect_walls
    except ImportError:  # ponytail: M1 fallback until walls/rooms/obstacles land
        rooms, doors = _m1_rooms(g, m)
        return [], rooms, doors, []
    walls = detect_walls(g, m, ceiling)
    rooms, doors = segment_rooms(g, m, walls)
    return walls, rooms, doors, detect_obstacles(g, m, walls)


def _room_at(rooms: list[dict], xz: list[float], tol: float = 0.6) -> str | None:
    best, bd = None, tol
    for r in rooms:
        d = Polygon(r["polygon"]).distance(Point(xz))
        if d <= bd:
            best, bd = r["id"], d
    return best


def _exits(openings: list[dict], rooms: list[dict], tags: list, start: int) -> tuple[list[dict], list[dict]]:
    """Exit doors from outside openings; tags [x, z, label] relabel the nearest within 1.5 m."""
    doors, exits = [], []
    for o in openings:
        rid = _room_at(rooms, o["center"])
        if rid is None:
            continue
        did = f"d_{start + len(doors):03d}"
        w = float(np.clip(o["width_m"], 0.6, 3.0))
        doors.append({"id": did, "level": LEVEL, "segment": o["segment"], "width_m": round(w, 3),
                      "connects": [rid, "outside"], "is_exit": True, "kind": "opening",
                      "fire_rated": None, "opens_toward": "outside"})
        exits.append({"id": f"e_{len(exits) + 1:03d}", "level": LEVEL, "door_id": did, "position": o["center"],
                      "width_m": round(w, 3), "leads_to": "outside", "label": f"Exit {len(exits) + 1}",
                      "assembly_point": None})
    for x, z, label in tags:
        if exits:
            e = min(exits, key=lambda e: np.hypot(e["position"][0] - x, e["position"][1] - z))
            if np.hypot(e["position"][0] - x, e["position"][1] - z) <= 1.5:
                e["label"] = str(label)
    return doors, exits


def _debug(out: Path, g: G.Grid, m: G.Masks, scene: dict) -> None:
    d = out / "debug"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("floor_supported", "occupied", "unknown", "outside", "free", "walkable"):
        cv2.imwrite(str(d / f"{name}.png"), getattr(m, name).astype(np.uint8) * 255)
    occ = np.clip(np.log1p(g.body) / np.log1p(max(1, g.body.max())) * 255, 0, 255).astype(np.uint8)
    cv2.imwrite(str(d / "occupancy.png"), cv2.applyColorMap(occ, cv2.COLORMAP_INFERNO))
    img = np.full(g.shape + (3,), 255, np.uint8)
    img[m.free] = (215, 235, 215)
    img[m.walkable] = (140, 210, 140)
    img[m.occupied] = (60, 60, 60)
    img[m.unknown & ~m.outside] = (120, 160, 230)

    def px(xz: list[float]) -> tuple[int, int]:
        r, c = g.rc(np.array(xz))[0]
        return int(c), int(r)

    for w in scene["walls"]:
        cv2.polylines(img, [np.array([px(p) for p in w["polygon"]], np.int32)], True, (0, 0, 0), 1)
    for o in scene["obstacles"]:
        cv2.polylines(img, [np.array([px(p) for p in o["polygon"]], np.int32)], True, (0, 140, 255), 1)
    for door in scene["doors"]:
        color = (0, 0, 255) if door["is_exit"] else (255, 0, 0)
        cv2.line(img, px(door["segment"][0]), px(door["segment"][1]), color, 3)
    for r in scene["rooms"]:
        cv2.putText(img, r["id"], px(r["centroid"]), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
    cv2.imwrite(str(d / "overview.png"), img)
    feats = [{"type": "Feature", "properties": {"kind": "room", "id": r["id"]},
              "geometry": {"type": "Polygon", "coordinates": [r["polygon"] + [r["polygon"][0]]]}}
             for r in scene["rooms"]]
    feats += [{"type": "Feature", "properties": {"kind": "door", "id": dd["id"], "is_exit": dd["is_exit"]},
               "geometry": {"type": "LineString", "coordinates": dd["segment"]}} for dd in scene["doors"]]
    (d / "scene.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))


def _load_inputs(corrections: Path | None, exit_tags: Path | None, input_path: Path) -> tuple[dict, list]:
    corr = json.loads(Path(corrections).read_text()) if corrections else {}
    tag_path = Path(exit_tags) if exit_tags else input_path.parent / "exit_tags.json"
    tags = corr.get("exit_tags") or (json.loads(tag_path.read_text()) if tag_path.exists() else [])
    return corr, tags


def run(input_path: Path, out_dir: Path, no_llm: bool = True, corrections: Path | None = None,
        exit_tags: Path | None = None, up: str | None = None, scale: float | None = None) -> dict:
    """Run Path B on one scan; writes outputs to out_dir and returns the scene dict."""
    input_path, out_dir = Path(input_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if input_path.suffix.lower() == ".json":
        from .roomplan_to_scene import convert  # Path A

        return convert(input_path, out_dir, no_llm=no_llm)
    corr, tags = _load_inputs(corrections, exit_tags, input_path)
    scale = scale or corr.get("scale")
    scan = load_scan(input_path)
    if scale:
        scan = Scan(scan.vertices * scale, scan.faces, scan.colors, scan.info)
    o = orient(scan, up_override=up or corr.get("up"))
    warnings = []
    if not 2.2 <= o.ceiling_height_m <= 4.5:
        warnings.append(_warn("SCALE_LOW_CONFIDENCE", f"floor-to-ceiling {o.ceiling_height_m:.2f} m outside "
                              "[2.2, 4.5]; check units or pass --scale"))
    g = G.build_grid(G.sample_points(scan, o.matrix))
    m = G.build_masks(g)
    walls, rooms, doors, obstacles = _segment(g, m, o.ceiling_height_m)
    openings, wide = G.exit_openings(g, m)
    warnings += [_warn("LEAKY_WALL_SUSPECTED", f"{w['width_m']} m opening to unscanned space at {w['center']}")
                 for w in wide]
    exit_doors, exits = _exits(openings, rooms, tags, len(doors) + 1)
    doors = doors + exit_doors
    walk = G.vectorize(m.walkable, g)
    kept = exit_reachable_parts(walk, [e["position"] for e in exits]) if exits else walk
    if kept.area < walk.area - 0.05:
        warnings.append(_warn("ROOM_UNREACHABLE", f"dropped {walk.area - kept.area:.2f} m^2 of walkable "
                              "area that cannot reach an exit"))
    navmesh = navmesh_from_walkable({LEVEL: kept}, {LEVEL: 0.0})
    scene = {
        "schema_version": "1.0",
        "meta": {"name": input_path.stem, "source": "lidar", "source_file": input_path.name, "units": "meters",
                 "up_axis": "y", "scale_method": "manual" if scale else "arkit_metric",
                 "scale_confidence": 0.9 if 2.2 <= o.ceiling_height_m <= 4.5 else 0.3,
                 "generated_by": f"ingest_lidar {_git_sha()}",
                 "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "levels": [{"id": LEVEL, "name": "Scanned floor", "elevation_m": 0.0,
                    "ceiling_height_m": round(o.ceiling_height_m, 3)}],
        "walls": walls, "rooms": rooms, "doors": doors, "exits": exits, "stairs": [], "elevators": [],
        "obstacles": obstacles, "points_of_interest": [],
        "walkable": {LEVEL: walkable_to_contract(kept)}, "navmesh": navmesh, "links": [],
        "assets": {"display_model": "display.glb", "floorplan_overlay": "debug/overview.png",
                   "navmesh_debug": "navmesh_debug.glb"},
        "warnings": warnings,
        "extensions": {"lidar": {"input_to_scene": np.round(o.matrix, 6).tolist(), "load": scan.info,
                                 "orient": {k: v for k, v in o.report.items() if k != "cues"},
                                 "orient_cues": o.report["cues"]}},
    }
    if not no_llm:
        from .semantics import label_rooms

        scene = label_rooms(scene)
    scene["extensions"]["lidar"]["validation"] = validate(scene)
    (out_dir / "scene.json").write_text(json.dumps(scene, indent=1))
    (out_dir / "validation.json").write_text(json.dumps(scene["extensions"]["lidar"]["validation"], indent=1))
    _debug(out_dir, g, m, scene)
    write_display(out_dir / "display.glb", scan, o.matrix, walls)
    write_navmesh_debug(out_dir / "navmesh_debug.glb", navmesh, doors)
    return scene
