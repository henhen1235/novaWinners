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
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from contract.validate_scene import validate

from . import grid as G
from .display import write_display, write_navmesh_debug
from .geometry import exit_reachable_parts, navmesh_from_walkable, ring, walkable_to_contract
from .load import Scan, load_scan
from .orient import apply, orient

LEVEL = "L1"
PLAUSIBLE_CEILING_M = (1.0, 10.0)  # outside this the units are wrong, not the building (D22)
UNIT_SCALES = {0.001: "mm", 0.01: "cm", 0.0254: "inches", 0.3048: "feet"}
SLIVER_M = 0.01  # walkable necks < 2 cm become degenerate navmesh triangles (D23)


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


def _confirm_exits(scan: Scan, matrix: np.ndarray, g: G.Grid, openings: list[dict], tagged: bool,
                   min_faces: int = 20) -> tuple[list[dict], list[dict]]:
    """Split openings into (confirmed, unconfirmed) by ARKit door faces on the opening.

    Door-labeled faces (class 7) at 0.3-2.0 m within 0.3 m of the opening segment confirm it.
    Without labels, or if nothing is confirmed and no tags exist, all openings stay exits
    (best effort, the scene must have an exit).
    """
    if scan.face_classes is None or not openings:
        return openings, []
    sel = scan.face_classes == 7
    c = apply(matrix, scan.vertices[scan.faces[sel]].mean(1)) if sel.any() else np.zeros((0, 3))
    c = c[(c[:, 1] > 0.3) & (c[:, 1] < 2.0)]
    confirmed, unconfirmed = [], []
    for op in openings:
        seg = LineString(op["segment"])
        n = sum(1 for x, z in c[:, [0, 2]] if seg.distance(Point(x, z)) <= 0.3) if len(c) else 0
        (confirmed if n >= min_faces else unconfirmed).append(op)
    if not confirmed and not tagged:
        return openings, []
    return confirmed, unconfirmed


def _drop_unreachable_slivers(rooms: list[dict], doors: list[dict], max_m2: float = 2.0
                              ) -> tuple[list[dict], list[dict], list[dict]]:
    """Drop rooms under max_m2 with no door path to an exit (scan pockets, not real rooms)."""
    import networkx as nx

    gr = nx.Graph()
    gr.add_nodes_from([r["id"] for r in rooms] + ["outside"])
    gr.add_edges_from(tuple(d["connects"]) for d in doors)
    goals = {c for d in doors if d["is_exit"] for c in d["connects"]}
    ok = set().union(*(nx.node_connected_component(gr, s) for s in goals if s in gr)) if goals else set()
    drop = [r for r in rooms if r["id"] not in ok and r["area_m2"] < max_m2] if goals else []
    gone = {r["id"] for r in drop}
    return ([r for r in rooms if r["id"] not in gone],
            [d for d in doors if not gone & set(d["connects"])], drop)


def _room_at(rooms: list[dict], xz: list[float], tol: float = 0.6) -> str | None:
    best, bd = None, tol
    for r in rooms:
        d = Polygon(r["polygon"]).distance(Point(xz))
        if d <= bd:
            best, bd = r["id"], d
    return best


def _exits(openings: list[dict], rooms: list[dict], tags: list, start: int) -> tuple[list[dict], list[dict]]:
    """Exit doors from outside openings.

    Without tags every opening is an exit. With tags [x, z, label] the tags decide: each tag
    picks the nearest opening within 2 m (any width), and only tagged openings become exits.
    """
    labels: dict[int, str] = {}
    if tags and openings:
        for x, z, label in tags:
            d = [np.hypot(o["center"][0] - x, o["center"][1] - z) for o in openings]
            if min(d) <= 2.0:
                labels[int(np.argmin(d))] = str(label)
        if labels:
            openings = [openings[i] for i in sorted(labels)]
            labels = {k: labels[i] for k, i in enumerate(sorted(labels))}
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
    if labels:
        exits = [{**e, "label": labels.get(i, e["label"])} for i, e in enumerate(exits)]
    return doors, exits


def _drop_navmesh_islands(nav: dict, exit_xz: list[list[float]], tol_m: float = 1.0) -> tuple[dict, float]:
    """Remove triangle components (joined by shared vertices, the validator's D5 rule) with no
    triangle within tol_m of an exit. Earcut leaves T-junctions where a ring runs collinear
    with itself, so one walkable polygon can triangulate into vertex-disjoint pieces.
    Returns (navmesh, dropped area m^2)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    tri = np.asarray(nav["triangles"], np.int64).reshape(-1, 3)
    if not len(tri) or not exit_xz:
        return nav, 0.0
    v = np.asarray(nav["vertices"], float)[:, [0, 2]]
    rows = np.repeat(np.arange(len(tri)), 3)
    adj = coo_matrix((np.ones(len(rows)), (rows, tri.ravel())), shape=(len(tri), len(v))).tocsr()
    _, comp = connected_components(adj @ adj.T, directed=False)
    p = v[tri]
    a, b = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    area = 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
    polys = shapely.polygons(p)
    near = np.zeros(len(tri), bool)
    for e in exit_xz:
        near |= shapely.distance(polys, Point(e)) <= tol_m
    keep = np.isin(comp, np.unique(comp[near]))
    if keep.all() or not keep.any():
        return nav, 0.0
    return {**nav, "triangles": tri[keep].tolist(),
            "triangle_level": [lv for lv, k in zip(nav["triangle_level"], keep) if k]}, float(area[~keep].sum())


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
    if input_path.suffix.lower() == ".json" or input_path.is_dir():
        from .roomplan_to_scene import convert  # Path A

        return convert(input_path, out_dir, no_llm=no_llm, exit_tags=Path(exit_tags) if exit_tags else None)
    corr, tags = _load_inputs(corrections, exit_tags, input_path)
    scale = scale or corr.get("scale")
    scan = load_scan(input_path)
    if scale:
        scan = Scan(scan.vertices * scale, scan.faces, scan.colors, scan.info)
    # ARKit meshes are gravity aligned (world +Y is up): use that instead of estimating it.
    arkit_up = "+y" if scan.face_classes is not None else None
    o = orient(scan, up_override=up or corr.get("up") or arkit_up)
    if not scale and not PLAUSIBLE_CEILING_M[0] <= o.ceiling_height_m <= PLAUSIBLE_CEILING_M[1]:
        # a 5 cm grid over a scan in cm would need ~10^4 x more cells; stop with a hint instead
        # up detection itself uses metric gates and may be wrong here; a storey's smallest extent is its height
        ext = min(c["ext"] for c in o.report["cues"] if c["ext"] > 0)
        f = min(UNIT_SCALES, key=lambda k: abs(np.log(ext * k / 2.7)))
        raise ValueError(f"SCALE_LOW_CONFIDENCE: floor-to-ceiling {ext:.2f} file units is not a "
                         f"room height in meters; if the file is in {UNIT_SCALES[f]}, pass --scale {f}")
    warnings = []
    if not 2.2 <= o.ceiling_height_m <= 4.5:
        warnings.append(_warn("SCALE_LOW_CONFIDENCE", f"floor-to-ceiling {o.ceiling_height_m:.2f} m outside "
                              "[2.2, 4.5]; check units or pass --scale"))
    g = G.build_grid(G.sample_points(scan, o.matrix), ceiling_m=o.ceiling_height_m)
    # Interior glass (Mesh mode): LiDAR sees the next room's floor through glass and nothing at
    # body height, so walkable space would run straight through a glass partition and each pane
    # between frames would read as a door. ARKit labels those surfaces "window" (6): block them
    # at body height, except where it also labels a door (7), bridging gaps between frames.
    glass = G.class_mask(scan, o.matrix, g, (6,), (0.15, 2.0), bridge_m=1.2)
    glass &= ~(cv2.dilate(G.class_mask(scan, o.matrix, g, (7,), (0.3, 2.0)).astype(np.uint8),
                          np.ones((7, 7), np.uint8)) > 0)
    m = G.build_masks(g, barrier=glass if glass.any() else None)
    walls, rooms, doors, obstacles = _segment(g, m, o.ceiling_height_m)
    openings, wide = G.exit_openings(g, m)
    # Glass walls: LiDAR sees through glass, so a curtain wall split by mullions looks like a row
    # of door-width openings to unscanned space. With ARKit labels (Mesh mode) an opening is an
    # exit only if door-labeled faces lie on it; human exit tags always win (see _exits).
    openings, unconfirmed = _confirm_exits(scan, o.matrix, g, openings, bool(tags))
    warnings += [_warn("EXIT_UNCONFIRMED", f"{op['width_m']} m opening at {op['center']} has no ARKit door "
                       "on it (glass or scan edge?); not an exit unless tagged") for op in unconfirmed]
    if tags:
        openings = openings + unconfirmed + wide  # tags pick among every opening
    warnings += [_warn("LEAKY_WALL_SUSPECTED", f"{w['width_m']} m opening to unscanned space at {w['center']}")
                 for w in wide]
    exit_doors, exits = _exits(openings, rooms, tags, len(doors) + 1)
    doors = doors + exit_doors
    if rooms and exits:  # walkable is the ground truth of passability (D28)
        from .rooms import connect_unreachable

        doors = doors + connect_unreachable(g, m, rooms, doors, len(doors) + 1)
    rooms, doors, dropped_rooms = _drop_unreachable_slivers(rooms, doors)
    warnings += [_warn("SMALL_ROOM_ARTIFACT", f"dropped {r['area_m2']} m^2 pocket with no path to an exit",
                       r["id"]) for r in dropped_rooms]
    walk = G.vectorize(m.walkable, g)
    # vectorize's simplify can pinch a narrow neck to zero width; its collinear triangles are
    # dropped as degenerate and the navmesh splits (NAVMESH_ISLAND). A mitre opening removes
    # necks < 2 cm without adding arc vertices that round onto each other.
    walk = shapely.set_precision(walk.buffer(-SLIVER_M, join_style="mitre").buffer(SLIVER_M, join_style="mitre"),
                                 0.001)
    walk = MultiPolygon([p for p in getattr(walk, "geoms", [walk]) if isinstance(p, Polygon) and p.area > 0.01])
    kept = exit_reachable_parts(walk, [e["position"] for e in exits]) if exits else walk
    if kept.area < walk.area - 0.05:
        warnings.append(_warn("ROOM_UNREACHABLE", f"dropped {walk.area - kept.area:.2f} m^2 of walkable "
                              "area that cannot reach an exit"))
    navmesh, dropped = _drop_navmesh_islands(navmesh_from_walkable({LEVEL: kept}, {LEVEL: 0.0}),
                                             [e["position"] for e in exits])
    if dropped:
        warnings.append(_warn("ROOM_UNREACHABLE", f"dropped {dropped:.2f} m^2 of navmesh triangles that only "
                              "touch the rest at T-junctions"))
    i2s = o.matrix @ np.diag([scale or 1.0] * 3 + [1.0])  # true file -> scene map, including --scale
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
        "extensions": {"lidar": {"input_to_scene": np.round(i2s, 6).tolist(), "load": scan.info,
                                 "orient": {k: v for k, v in o.report.items() if k != "cues"},
                                 "orient_cues": o.report["cues"],
                                 "grid": {"x0": g.x0, "z0": g.z0, "res": g.res,
                                          "png": "debug/overview.png"}}},
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
