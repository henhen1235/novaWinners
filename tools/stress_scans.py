"""Stress scans: layouts and capture conditions our Manhattan generator never makes.

Truth scenes are built from arbitrary room polygons (wall CENTERLINE polygons,
meters, XZ) and door segments, so walls may be diagonal or curved. Scans come
from tools.make_synthetic_scan.make_scan, then optional capture post-processing
(Z-up export, centimeter units, point cloud, crop). See research/GENERALIZATION.md.

    python -m tools.stress_scans --make data/stress
    python -m tools.stress_scans --score data/stress -o out/stress
"""

from __future__ import annotations

import argparse
import json
import math
import time
import traceback
from pathlib import Path

import numpy as np
import trimesh
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from contract.validate_scene import validate
from ingest_lidar.geometry import door_gap_box, navmesh_from_walkable, ring, walkable_from_parts, walkable_to_contract
from tools import layouts
from tools.make_synthetic_scan import make_scan

T = 0.15
R = 0.25
FIXED_TIME = "1970-01-01T00:00:00Z"


# --- generic truth builder ------------------------------------------------------------


def _edges(poly: Polygon) -> list[tuple[tuple, tuple]]:
    c = [tuple(np.round(p, 4)) for p in poly.exterior.coords]
    return [tuple(sorted((a, b))) for a, b in zip(c[:-1], c[1:]) if a != b]


def build_truth(name: str, rooms: list[tuple[str, str, Polygon]], doors: list[tuple[list, str | None]],
                ceiling: float = 3.0, obstacles: list[tuple[Polygon, float, str]] = (), t: float = T) -> dict:
    """Contract scene from wall-centerline room polygons and door segments.

    rooms: (id, type, centerline polygon); interiors are shrunk by t/2.
    doors: ([[x, z], [x, z]] on a room edge, exit label or None).
    obstacles: (footprint, height, kind). Walls: one rectangle per unique room
    edge (square caps close corners), minus door openings. Raises if the result
    fails the contract validator.
    """
    interiors = [(rid, typ, poly.buffer(-t / 2, join_style="mitre")) for rid, typ, poly in rooms]
    cuts = unary_union([door_gap_box(seg, t + 0.04) for seg, _ in doors]) if doors else Polygon()
    walls = []
    for a, b in sorted({e for _, _, poly in rooms for e in _edges(poly)}):
        rect = LineString([a, b]).buffer(t / 2, cap_style="square", join_style="mitre").difference(cuts)
        for p in getattr(rect, "geoms", [rect]):
            if isinstance(p, Polygon) and p.area > 0.01:
                walls.append({"id": f"wall_{len(walls) + 1:03d}", "level": "L1", "polygon": ring(p),
                              "height_m": ceiling})
    cdoors, exits = [], []
    for i, (seg, label) in enumerate(doors, 1):
        p0, p1 = np.asarray(seg, float)
        d = p1 - p0
        n = np.array([-d[1], d[0]]) / np.linalg.norm(d)
        mid = (p0 + p1) / 2
        side = [next((rid for rid, _, ip in interiors if ip.covers(Point(mid + s * n * (t / 2 + 0.1)))), "outside")
                for s in (-1, 1)]
        if side[0] == side[1]:
            raise ValueError(f"{name}: door {i} has {side[0]} on both sides")
        width = float(np.linalg.norm(d))
        did = f"door_{i:02d}"
        cdoors.append({"id": did, "level": "L1", "segment": [p0.tolist(), p1.tolist()], "width_m": width,
                       "connects": side, "is_exit": "outside" in side,
                       "kind": "double" if width >= 1.5 else "swing", "fire_rated": None,
                       "opens_toward": "outside" if "outside" in side else None})
        if "outside" in side:
            exits.append({"id": f"exit_{len(exits) + 1:02d}", "level": "L1", "door_id": did,
                          "position": mid.tolist(), "width_m": width, "leads_to": "outside",
                          "label": label or f"Exit {len(exits) + 1}", "assembly_point": None})
    obs = [{"id": f"obs_{i:02d}", "level": "L1", "polygon": ring(p), "height_m": h, "kind": k, "passable": False}
           for i, (p, h, k) in enumerate(obstacles, 1)]
    free = [ip for _, _, ip in interiors] + [door_gap_box(d["segment"], t) for d in cdoors]
    walk = walkable_from_parts(free, [p for p, _, _ in obstacles], R)
    scene = layouts._round({
        "schema_version": "1.0",
        "meta": {"name": name, "source": "synthetic", "source_file": f"tools.stress_scans:{name}",
                 "units": "meters", "up_axis": "y", "scale_method": "manual", "scale_confidence": 1.0,
                 "generated_by": "tools.stress_scans", "generated_at": FIXED_TIME},
        "levels": [{"id": "L1", "name": "Ground", "elevation_m": 0.0, "ceiling_height_m": ceiling}],
        "walls": walls,
        "rooms": [{"id": rid, "level": "L1", "name": rid, "type": typ, "polygon": ring(ip), "area_m2": ip.area,
                   "centroid": [ip.centroid.x, ip.centroid.y], "has_window": False} for rid, typ, ip in interiors],
        "doors": cdoors, "exits": exits, "stairs": [], "elevators": [], "obstacles": obs,
        "points_of_interest": [], "walkable": {"L1": walkable_to_contract(walk)},
        "navmesh": navmesh_from_walkable({"L1": walk}, {"L1": 0.0}, R), "links": [],
        "assets": {"display_model": "display.glb", "floorplan_overlay": "debug/overview.png",
                   "navmesh_debug": "navmesh_debug.glb"},
        "warnings": [], "extensions": {},
    })
    errors = [i for i in validate(scene) if i["severity"] == "error"]
    if errors:
        raise ValueError(f"{name}: truth fails the validator: {errors}")
    return scene


def _hseg(x: float, z: float, w: float) -> list:
    return [[x - w / 2, z], [x + w / 2, z]]


def _vseg(x: float, z: float, w: float) -> list:
    return [[x, z - w / 2], [x, z + w / 2]]


def _corridor_block(width_c: float = 2.0, door_w: float = 0.9) -> tuple[list, list]:
    """12 m corridor (z 3..3+width_c) with three 4 x 3 m rooms per side, west exit."""
    z1 = 3 + width_c
    rooms = [("corridor", "corridor", box(0, 3, 12, z1))]
    doors = [(_vseg(0, 3 + width_c / 2, 1.2), "West")]
    for i, x in enumerate((0, 4, 8)):
        rooms += [(f"n{i}", "office", box(x, 0, x + 4, 3)), (f"s{i}", "office", box(x, z1, x + 4, z1 + 3))]
        doors += [(_hseg(x + 2, 3, door_w), None), (_hseg(x + 2, z1, door_w), None)]
    return rooms, doors


def rotated_wing() -> dict:
    """Corridor block plus a two-room wing at 45 deg off its east end; all rotated 30 deg."""
    rooms, doors = _corridor_block()
    c, s = math.cos(math.radians(45)), math.sin(math.radians(45))

    def wing(u0: float, u1: float) -> Polygon:
        return Polygon([(11 + u * c - v * s, 4 + u * s + v * c) for u, v in ((u0, -2), (u1, -2), (u1, 2), (u0, 2))])

    a = wing(0, 4).intersection(box(12, -10, 30, 30))
    rooms += [("wing_a", "office", a), ("wing_b", "office", wing(4, 8))]

    def seg(u: float, w: float) -> list:
        return [[11 + u * c + w / 2 * s, 4 + u * s - w / 2 * c], [11 + u * c - w / 2 * s, 4 + u * s + w / 2 * c]]

    doors += [(_vseg(12, 4, 0.9), None), (seg(4, 0.9), None), (seg(8, 1.0), "Wing exit")]
    rot = [(rid, typ, affinity.rotate(p, 30, origin=(0, 0))) for rid, typ, p in rooms]
    rdoors = [([list(affinity.rotate(Point(q), 30, origin=(0, 0)).coords[0]) for q in sg], lab) for sg, lab in doors]
    return build_truth("rotated_wing", rot, rdoors)


def l_corridor() -> dict:
    """L-shaped 2 m corridor with rooms on its inner side and an exit at each end."""
    corr = Polygon([(0, 0), (14, 0), (14, 2), (2, 2), (2, 12), (0, 12)])
    rooms = [("corridor", "corridor", corr)]
    doors = [(_vseg(14, 1, 1.0), "East"), (_hseg(1, 12, 1.0), "North")]
    for i, x in enumerate((2, 6, 10)):
        rooms.append((f"a{i}", "office", box(x, 2, x + 4, 6)))
        doors.append((_hseg(x + 2, 2, 0.9), None))
    for i, z in enumerate((6, 9)):
        rooms.append((f"b{i}", "office", box(2, z, 6, z + 3)))
        doors.append((_vseg(2, z + 1.5, 0.9), None))
    return build_truth("l_corridor", rooms, doors)


def open_plan() -> dict:
    """16 x 11 m open-plan room with six 0.5 m pillars and one side office."""
    rooms = [("hall", "office", box(0, 0, 16, 11)), ("office", "office", box(16, 0, 20, 4))]
    doors = [(_vseg(0, 5.5, 1.8), "West"), (_hseg(8, 11, 1.2), "South"), (_vseg(16, 2, 0.9), None)]
    pillars = [(box(x - 0.25, z - 0.25, x + 0.25, z + 0.25), 3.0, "pillar") for x in (4, 8, 12) for z in (3.7, 7.3)]
    return build_truth("open_plan", rooms, doors, obstacles=pillars)


def curved_wall() -> dict:
    """6 x 6 m room whose east side is a semicircle (r 3 m, 16 segments), plus a corridor."""
    arc = [(6 + 3 * math.sin(a), 3 - 3 * math.cos(a)) for a in np.linspace(0, math.pi, 17)]
    room = Polygon([(0, 0)] + arc + [(0, 6)])
    rooms = [("round", "office", room), ("corridor", "corridor", box(-2, 0, 0, 10)),
             ("store", "office", box(0, 6, 6, 10))]
    doors = [(_vseg(0, 3, 0.9), None), (_vseg(0, 8, 0.9), None), (_hseg(-1, 10, 1.2), "North"),
             (_hseg(-1, 0, 1.0), "South")]
    return build_truth("curved_wall", rooms, doors)


def closets() -> dict:
    """Six 1.5 m wide closets (0.8 m doors) off a corridor, an office opposite."""
    rooms = [("corridor", "corridor", box(0, 2.5, 9, 4.5)), ("office", "office", box(0, 4.5, 9, 8))]
    doors = [(_vseg(0, 3.5, 1.0), "West"), (_hseg(4.5, 4.5, 0.9), None)]
    for i in range(6):
        rooms.append((f"closet{i}", "storage", box(1.5 * i, 0, 1.5 * i + 1.5, 2.5)))
        doors.append((_hseg(1.5 * i + 0.75, 2.5, 0.8), None))
    return build_truth("closets", rooms, doors)


def double_doors() -> dict:
    """Corridor block whose six room doors are 1.8 m double doors."""
    rooms, doors = _corridor_block(door_w=1.8)
    return build_truth("double_doors", rooms, doors)


def wide_corridor() -> dict:
    """Corridor block with a 2.4 m corridor."""
    rooms, doors = _corridor_block(width_c=2.4)
    return build_truth("wide_corridor", rooms, doors)


def tiny_office(ceiling: float = 3.0) -> dict:
    spec = layouts.tiny_office()
    spec["levels"] = [{**lv, "ceiling_height_m": ceiling} for lv in spec["levels"]]
    return layouts.build_scene(spec)


# --- capture post-processing --------------------------------------------------------------


def _rewrite(d: Path, new_pose: np.ndarray, point_cloud: bool = False, crop=None) -> None:
    """Replace the random pose of d/scan.* with new_pose (truth->file 4x4); optionally
    drop faces whose truth-frame centroid satisfies crop(xz) or write a point cloud."""
    old = np.asarray(json.loads((d / "truth_transform.json").read_text())["truth_to_mesh"])
    mesh = trimesh.load(d / "scan.obj", force="mesh", process=False)
    mesh.apply_transform(np.linalg.inv(old))  # back to the truth frame
    if crop is not None:
        c = mesh.triangles_center
        mesh.update_faces(~crop(c[:, [0, 2]]))
        mesh.remove_unreferenced_vertices()
    mesh.apply_transform(new_pose)
    for f in ("scan.obj", "scan.ply"):
        (d / f).unlink(missing_ok=True)
    if point_cloud:
        pts, _ = trimesh.sample.sample_surface(mesh, int(1500 * mesh.area), seed=0)
        trimesh.PointCloud(pts).export(d / "scan.ply")
    else:
        mesh.export(d / "scan.obj", include_normals=False)
    (d / "truth_transform.json").write_text(json.dumps({"truth_to_mesh": new_pose.tolist()}, indent=1) + "\n")
    p = json.loads((d / "scan_params.json").read_text())
    (d / "scan_params.json").write_text(json.dumps({**p, "point_cloud": point_cloud, "triangles": len(mesh.faces)}))


def _zup(scale: float = 1.0) -> np.ndarray:
    """Truth (Y up) -> file with +Z up: (x, y, z) -> (x, -z, y), times scale, shifted."""
    m = np.array([[1, 0, 0, 2.0], [0, 0, -1, -3.0], [0, 1, 0, 0.5], [0, 0, 0, 1]])
    return np.diag([scale, scale, scale, 1.0]) @ m


# name -> (truth factory, make_scan kwargs, post-processing, pipeline kwargs)
CASES = {
    "rotated_wing": (rotated_wing, {}, None, {}),
    "l_corridor": (l_corridor, {}, None, {}),
    "open_plan": (open_plan, {}, None, {}),
    "curved_wall": (curved_wall, {}, None, {}),
    "closets": (closets, {}, None, {}),
    "double_doors": (double_doors, {}, None, {}),
    "wide_corridor": (wide_corridor, {}, None, {}),
    "ceiling_2_3": (lambda: tiny_office(2.3), {}, None, {}),
    "ceiling_4_0": (lambda: tiny_office(4.0), {}, None, {}),
    "z_up": (tiny_office, {}, lambda d: _rewrite(d, _zup()), {}),
    "centimeters": (tiny_office, {}, lambda d: _rewrite(d, _zup(100.0)), {}),
    "centimeters_scaled": (tiny_office, {}, lambda d: _rewrite(d, _zup(100.0)), {"scale": 0.01}),
    "point_cloud": (tiny_office, {}, lambda d: _rewrite(d, _zup(), point_cloud=True), {}),
    "cropped_room": (tiny_office, {}, lambda d: _rewrite(d, np.eye(4), crop=lambda xz: (xz[:, 0] > 13.6) & (xz[:, 1] > 6.2)), {}),
    "holes_20": (tiny_office, {"hole_frac": 0.20}, None, {}),
    "noise_5cm": (tiny_office, {"noise_sigma_m": 0.05}, None, {}),
}


def make_all(root: Path, only: list[str] | None = None) -> None:
    for name, (factory, kw, post, _) in CASES.items():
        if only and name not in only:
            continue
        d = root / name
        start = time.perf_counter()
        make_scan(factory(), seed=7, out_dir=d, **kw)
        if post:
            post(d)
        print(f"{d}: {time.perf_counter() - start:.1f} s")
    for ifc in sorted(root.glob("ifc_*")):  # written by tools.ifc_to_scene, if present
        print(f"{ifc}: kept")


def score_all(root: Path, out: Path, only: list[str] | None = None) -> list[dict]:
    from ingest_lidar.pipeline import run
    from tools.score_against_truth import score

    rows = []
    for d in sorted(p for p in root.iterdir() if (p / "truth.scene.json").exists()):
        if only and d.name not in only:
            continue
        kw = CASES.get(d.name, (None, None, None, {}))[3]
        scan = d / "scan.obj" if (d / "scan.obj").exists() else d / "scan.ply"
        row = {"case": d.name}
        start = time.perf_counter()
        try:
            scene = run(scan, out / d.name, no_llm=True, **kw)
            row["runtime_s"] = round(time.perf_counter() - start, 2)
            s = score(scene, d)
            row.update({k: s[k] for k in ("up_error_deg", "yaw_deg", "walkable_iou", "door_recall", "door_precision",
                                          "room_count_diff", "rooms_truth", "doors_truth", "validator_error_codes")})
            row["warnings"] = sorted({w["code"] for w in scene["warnings"]})
        except Exception as exc:  # one broken case must not stop the run
            row.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "traceback"}, default=str))
    out.mkdir(parents=True, exist_ok=True)
    (out / "stress.json").write_text(json.dumps(rows, indent=1, default=str))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--make", type=Path)
    ap.add_argument("--score", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("out/stress"))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args(argv)
    if a.make:
        make_all(a.make, a.only)
    if a.score:
        score_all(a.score, a.out, a.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
