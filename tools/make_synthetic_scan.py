"""Synthetic LiDAR-like scans from ground-truth layouts (PLAN.md, "Synthetic scan
generator spec").

Frames and units: meters. The truth frame is the contract frame (Y up, floor at
y = elevation, 2D points [x, z]). The written mesh is in a "mesh" frame:
mesh_point = truth_to_mesh @ truth_point (homogeneous, 4x4 row-major in
truth_transform.json), a random proper rotation plus a translation of up to
10 m, so the pipeline has to find up and the floor plan itself.

    python -m tools.make_synthetic_scan --layout tiny_office --seed 0 -o data/synth/x
    python -m tools.make_synthetic_scan --bench data/synth     # 30-scan benchmark

Each output dir holds scan.obj, scan.ply, truth.scene.json (truth plus added
furniture, walkable and navmesh recomputed, validator-clean),
truth_transform.json and scan_params.json (layout, seed, damage settings).
Everything is driven by numpy.random.default_rng(seed), so output is
deterministic.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import time
from pathlib import Path

import numpy as np
import shapely
import trimesh
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import nearest_points, unary_union

from contract.validate_scene import validate
from ingest_lidar.geometry import (
    door_gap_box,
    exit_reachable_parts,
    navmesh_from_walkable,
    ring,
    walkable_from_parts,
    walkable_to_contract,
)
from tools import layouts

MAX_EDGE_M = 0.2
CELL_M = MAX_EDGE_M / math.sqrt(2) - 1e-3  # grid cell whose diagonal stays <= 0.2 m
MAX_TRIANGLES = 400_000
LINTEL_Y_M = 2.1
DOOR_CLEAR_M = 1.0
FURNITURE_GAP_M = 0.1
MAX_TRANSLATION_M = 10.0
MAX_PLACEMENT_TRIES = 40
# Face labels, used to aim holes and ceiling patches.
FLOOR, CEILING, WALL, LINTEL, FURNITURE = range(5)

# (width range, depth range, height range) in meters.
FURNITURE_SIZES = {
    "desk": ((1.2, 1.6), (0.6, 0.8), (0.75, 0.75)),
    "cabinet": ((0.8, 1.2), (0.4, 0.5), (1.8, 2.0)),
    "table": ((1.0, 2.0), (0.8, 1.0), (0.75, 0.75)),
}


# --- step 1: furniture and recomputed truth -----------------------------------


def _wall_thickness(scene: dict) -> float:
    """Median short side (m) of the wall rectangles; 0.15 when there are no walls."""
    sides = []
    for wall in scene["walls"]:
        x0, z0, x1, z1 = Polygon(wall["polygon"]).bounds
        sides.append(min(x1 - x0, z1 - z0))
    return float(np.median(sides)) if sides else 0.15


def _walkable(scene: dict, t: float, r: float) -> MultiPolygon:
    """Truth walkable (D6): rooms + door gaps minus non-passable obstacles, eroded by r."""
    free = [Polygon(rm["polygon"]) for rm in scene["rooms"]]
    free += [door_gap_box(d["segment"], t) for d in scene["doors"]]
    blockers = [Polygon(o["polygon"]) for o in scene["obstacles"] if not o["passable"]]
    return walkable_from_parts(free, blockers, r)


def _reaches_every_room(scene: dict, walkable: MultiPolygon) -> bool:
    """True when every walkable part reaches an exit and every room keeps walkable area."""
    exits = [e["position"] for e in scene["exits"]]
    reach = exit_reachable_parts(walkable, exits)
    if len(reach.geoms) != len(walkable.geoms):
        return False
    return all(Polygon(rm["polygon"]).intersection(reach).area >= 0.1 for rm in scene["rooms"])


def _candidate(rng: np.random.Generator, room: Polygon, kind: str) -> Polygon:
    """One random axis-aligned furniture footprint inside `room`'s bbox (XZ, m)."""
    (w0, w1), (d0, d1), _ = FURNITURE_SIZES[kind]
    w, d = rng.uniform(w0, w1), rng.uniform(d0, d1)
    x0, z0, x1, z1 = room.bounds
    if kind == "cabinet" and rng.random() < 0.7:
        # Against a wall: long side along the chosen wall, 2 cm gap.
        side = int(rng.integers(4))
        gap = 0.02
        if side < 2:  # wall along X (z = z0 or z1)
            cx = rng.uniform(x0 + w / 2, max(x0 + w / 2, x1 - w / 2))
            cz = z0 + gap + d / 2 if side == 0 else z1 - gap - d / 2
            return box(cx - w / 2, cz - d / 2, cx + w / 2, cz + d / 2)
        cz = rng.uniform(z0 + w / 2, max(z0 + w / 2, z1 - w / 2))
        cx = x0 + gap + d / 2 if side == 2 else x1 - gap - d / 2
        return box(cx - d / 2, cz - w / 2, cx + d / 2, cz + w / 2)
    if rng.random() < 0.5:
        w, d = d, w
    cx = rng.uniform(x0 + w / 2 + 0.1, max(x0 + w / 2 + 0.1, x1 - w / 2 - 0.1))
    cz = rng.uniform(z0 + d / 2 + 0.1, max(z0 + d / 2 + 0.1, z1 - d / 2 - 0.1))
    return box(cx - w / 2, cz - d / 2, cx + w / 2, cz + d / 2)


def _try_place(scene: dict, fp: Polygon, room: Polygon, clear: Polygon, t: float, r: float,
               height: float, kind: str) -> dict | None:
    """Scene with an obstacle at `fp` added, or None when the placement is rejected."""
    if not room.buffer(1e-6).contains(fp) or fp.intersects(clear):
        return None
    if any(fp.distance(Polygon(o["polygon"])) < FURNITURE_GAP_M for o in scene["obstacles"]):
        return None
    obstacle = {
        "id": f"obs_{len(scene['obstacles']) + 1:02d}", "level": scene["levels"][0]["id"],
        "polygon": ring(fp), "height_m": round(height, 3), "kind": kind, "passable": False,
    }
    trial = {**scene, "obstacles": scene["obstacles"] + [obstacle]}
    return trial if _reaches_every_room(trial, _walkable(trial, t, r)) else None


def add_furniture(truth: dict, rng: np.random.Generator) -> dict:
    """Copy of `truth` with 1-3 furniture boxes per non-corridor room, a 0.4 m pillar
    in the largest room, and walkable/navmesh recomputed (all XZ meters)."""
    if len(truth["levels"]) != 1 or truth.get("stairs"):
        raise ValueError("make_scan supports single-level layouts without stairs only")
    scene = copy.deepcopy(truth)
    level = scene["levels"][0]
    t, r = _wall_thickness(scene), scene["navmesh"]["agent_radius_m"]
    clear = unary_union([LineString(d["segment"]).buffer(DOOR_CLEAR_M, cap_style="flat") for d in scene["doors"]])
    rooms = [(rm, Polygon(rm["polygon"])) for rm in scene["rooms"]]
    for rm, poly in rooms:
        if rm["type"] == "corridor":
            continue
        for _ in range(int(rng.integers(1, 4))):
            kind = str(rng.choice(list(FURNITURE_SIZES)))
            h0, h1 = FURNITURE_SIZES[kind][2]
            height = rng.uniform(h0, h1)
            for _ in range(MAX_PLACEMENT_TRIES):
                placed = _try_place(scene, _candidate(rng, poly, kind), poly, clear, t, r, height, "furniture")
                if placed is not None:
                    scene = placed
                    break
    # Pillar in the largest room; if it cannot go there (a narrow corridor would be
    # blocked), try the next largest.
    for _, poly in sorted(rooms, key=lambda item: -item[1].area):
        x0, z0, x1, z1 = poly.bounds
        mx, mz = min(0.8, (x1 - x0) / 2 - 0.2), min(0.8, (z1 - z0) / 2 - 0.2)
        placed = None
        for _ in range(MAX_PLACEMENT_TRIES):
            cx, cz = rng.uniform(x0 + mx, x1 - mx), rng.uniform(z0 + mz, z1 - mz)
            fp = box(cx - 0.2, cz - 0.2, cx + 0.2, cz + 0.2)
            placed = _try_place(scene, fp, poly, clear, t, r, level["ceiling_height_m"], "pillar")
            if placed is not None:
                break
        if placed is not None:
            scene = placed
            break
    walkable = _walkable(scene, t, r)
    return {
        **scene,
        "walkable": {level["id"]: walkable_to_contract(walkable)},
        "navmesh": navmesh_from_walkable({level["id"]: walkable}, {level["id"]: level["elevation_m"]}, r),
    }


# --- step 2: clean mesh in the truth frame --------------------------------------


def _orient(tris: np.ndarray, normal: np.ndarray) -> np.ndarray:
    """Triangles (m, 3, 3) rewound so each face normal has a positive dot with `normal`."""
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    flip = n @ normal < 0
    return np.where(flip[:, None, None], tris[:, [0, 2, 1]], tris)


def _quad_grid(origin: np.ndarray, u: np.ndarray, v: np.ndarray, normal: np.ndarray, cell: float) -> np.ndarray:
    """Planar parallelogram origin + a*u + b*v (a, b in [0, 1]) as a triangle grid
    with cells at most `cell` m per side, wound to face `normal`."""
    nu = max(1, math.ceil(np.linalg.norm(u) / cell))
    nv = max(1, math.ceil(np.linalg.norm(v) / cell))
    a, b = np.meshgrid(np.linspace(0, 1, nu + 1), np.linspace(0, 1, nv + 1), indexing="ij")
    pts = origin + a[..., None] * u + b[..., None] * v  # (nu+1, nv+1, 3)
    p00, p10, p01, p11 = pts[:-1, :-1], pts[1:, :-1], pts[:-1, 1:], pts[1:, 1:]
    tris = np.concatenate([np.stack([p00, p10, p11], -2), np.stack([p00, p11, p01], -2)]).reshape(-1, 3, 3)
    return _orient(tris, normal)


def _polygons(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    return [geom] if isinstance(geom, Polygon) else [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def _sides(geom, y0: float, y1: float, cell: float) -> np.ndarray:
    """Vertical faces along every ring edge of `geom` (XZ) from y0 to y1, normals
    pointing out of the solid (no caps)."""
    out = []
    for poly in _polygons(geom):
        poly = orient(poly, 1.0)  # solid on the left of every ring, so outward = right
        for coords in [poly.exterior.coords] + [h.coords for h in poly.interiors]:
            c = np.asarray(coords)
            for (x0, z0), (x1, z1) in zip(c[:-1], c[1:]):
                dx, dz = x1 - x0, z1 - z0
                if math.hypot(dx, dz) < 1e-9:
                    continue
                out.append(_quad_grid(np.array([x0, y0, z0]), np.array([dx, 0, dz]),
                                      np.array([0, y1 - y0, 0]), np.array([dz, 0, -dx]), cell))
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def _plane(geom, y: float, up: bool, cell: float) -> np.ndarray:
    """Horizontal region `geom` (XZ) at height y as grid triangles, normal +Y or -Y."""
    if geom.is_empty:
        return np.zeros((0, 3, 3))
    x0, z0, x1, z1 = geom.bounds
    xs = np.arange(x0, x1, cell)
    zs = np.arange(z0, z1, cell)
    gx, gz = np.meshgrid(xs, zs, indexing="ij")
    gx, gz = gx.ravel(), gz.ravel()
    cells = shapely.box(gx, gz, np.minimum(gx + cell, x1), np.minimum(gz + cell, z1))
    shapely.prepare(geom)
    full = shapely.covers(geom, cells)
    part = ~full & shapely.intersects(geom, cells)
    # Full cells: two triangles each, as (x, z) corner quads.
    fx0, fz0 = gx[full], gz[full]
    fx1, fz1 = np.minimum(fx0 + cell, x1), np.minimum(fz0 + cell, z1)
    q = [np.stack([fx0, fz0], -1), np.stack([fx1, fz0], -1), np.stack([fx1, fz1], -1), np.stack([fx0, fz1], -1)]
    tris2 = [np.stack([q[0], q[1], q[2]], 1), np.stack([q[0], q[2], q[3]], 1)]
    for piece in shapely.intersection(cells[part], geom):
        for poly in _polygons(piece):
            if poly.area < 1e-8:
                continue
            verts, faces = trimesh.creation.triangulate_polygon(poly, engine="earcut")
            tris2.append(np.asarray(verts, float)[np.asarray(faces, int)])
    xz = np.concatenate(tris2)
    tris = np.stack([xz[..., 0], np.full(xz.shape[:2], y), xz[..., 1]], -1)
    return _orient(tris, np.array([0.0, 1.0 if up else -1.0, 0.0]))


def build_mesh(scene: dict, cell: float = CELL_M) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Clean scan surfaces of a single-level truth scene, truth frame (m, Y up).

    Floor (rooms + door gaps minus obstacle footprints, +Y), ceiling at H (-Y),
    walls without caps (0-2.1 m from the walls, 2.1-H from walls plus lintels so
    lintels close the space above doors), lintel undersides at 2.1 m (-Y),
    obstacle boxes without bottoms (pillars without tops either). Returns
    (vertices (n, 3), faces (m, 3), labels (m,)) with duplicate vertices merged.
    """
    level = scene["levels"][0]
    y0, height = level["elevation_m"], level["ceiling_height_m"]
    t = _wall_thickness(scene)
    rooms = unary_union([Polygon(rm["polygon"]) for rm in scene["rooms"]])
    gaps = [door_gap_box(d["segment"], t) for d in scene["doors"]]
    lintels = unary_union([door_gap_box(d["segment"], t - 0.02) for d in scene["doors"]])
    walls = unary_union([Polygon(w["polygon"]) for w in scene["walls"]])
    obstacles = [(Polygon(o["polygon"]), o["height_m"]) for o in scene["obstacles"]]
    full_height = unary_union([p for p, h in obstacles if h >= height - 1e-6])
    lintel_y = y0 + min(LINTEL_Y_M, height)
    parts = [
        (_plane(unary_union([rooms] + gaps).difference(unary_union([p for p, _ in obstacles])), y0, True, cell), FLOOR),
        (_plane(rooms.difference(full_height), y0 + height, False, cell), CEILING),
        (_sides(walls, y0, lintel_y, cell), WALL),
        (_sides(unary_union([walls, lintels]), lintel_y, y0 + height, cell), WALL),
        (_plane(lintels, lintel_y, False, cell), LINTEL),
    ]
    for poly, h in obstacles:
        top = min(h, height)
        parts.append((_sides(poly, y0, y0 + top, cell), FURNITURE))
        if top < height - 1e-6:
            parts.append((_plane(poly, y0 + top, True, cell), FURNITURE))
    tris = np.concatenate([p for p, _ in parts])
    labels = np.concatenate([np.full(len(p), lab) for p, lab in parts])
    verts, inverse = np.unique(np.round(tris.reshape(-1, 3), 6), axis=0, return_inverse=True)
    return verts, inverse.reshape(-1, 3), labels


# --- step 3: damage ---------------------------------------------------------------


def _face_geometry(verts: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    tri = verts[faces]
    return tri.mean(1), 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)


def _holes(scene: dict, centroids: np.ndarray, areas: np.ndarray, labels: np.ndarray,
           hole_frac: float, rng: np.random.Generator) -> np.ndarray:
    """Boolean mask of faces removed by spherical blobs (r 0.1-0.5 m) until hole_frac
    of the surface area is gone. Centers: 40% near furniture, 30% near room
    corners, 30% uniform (area-weighted)."""
    removed = np.zeros(len(areas), bool)
    if hole_frac <= 0:
        return removed
    tree = cKDTree(centroids)
    furniture = np.flatnonzero(labels == FURNITURE)
    corners = np.array([p for rm in scene["rooms"] for p in rm["polygon"]], float)
    height = scene["levels"][0]["ceiling_height_m"]
    target, weights = hole_frac * areas.sum(), areas / areas.sum()
    for _ in range(100_000):
        if areas[removed].sum() >= target:
            break
        pick = rng.random()
        if pick < 0.4 and len(furniture):
            f = rng.choice(furniture, p=areas[furniture] / areas[furniture].sum())
            center = centroids[f] + rng.normal(0, 0.2, 3)
        elif pick < 0.7 and len(corners):
            x, z = corners[rng.integers(len(corners))] + rng.normal(0, 0.1, 2)
            center = np.array([x, rng.uniform(0, height), z])
        else:
            center = centroids[rng.choice(len(areas), p=weights)]
        removed[tree.query_ball_point(center, rng.uniform(0.1, 0.5))] = True
    return removed


def _ceiling_patches(centroids: np.ndarray, labels: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Mask of 2-4 missing ceiling discs (radius 0.5-1.2 m in XZ)."""
    removed = np.zeros(len(labels), bool)
    ceiling = np.flatnonzero(labels == CEILING)
    if not len(ceiling):
        return removed
    for _ in range(int(rng.integers(2, 5))):
        c = centroids[rng.choice(ceiling)]
        d = np.hypot(centroids[ceiling, 0] - c[0], centroids[ceiling, 2] - c[2])
        removed[ceiling[d <= rng.uniform(0.5, 1.2)]] = True
    return removed


def _phantoms(scene: dict, rng: np.random.Generator, cell: float) -> np.ndarray:
    """3 small vertical patches (0.3-0.8 m) 1-3 m outside the building, facing it
    ("glass" reflections). Triangles (m, 3, 3), truth frame."""
    footprint = unary_union([Polygon(w["polygon"]) for w in scene["walls"]] + [Polygon(r["polygon"]) for r in scene["rooms"]])
    x0, z0, x1, z1 = footprint.bounds
    out = []
    while len(out) < 3:
        p = Point(rng.uniform(x0 - 3, x1 + 3), rng.uniform(z0 - 3, z1 + 3))
        if not 1.0 <= footprint.distance(p) <= 3.0:
            continue
        near = nearest_points(footprint, p)[0]
        facing = np.array([near.x - p.x, near.y - p.y])
        facing /= np.linalg.norm(facing)
        s, yc = rng.uniform(0.3, 0.8), rng.uniform(0.5, 2.0)
        u = np.array([-facing[1], 0, facing[0]]) * s
        v = np.array([0, s, 0])
        origin = np.array([p.x, yc, p.y]) - u / 2 - v / 2
        out.append(_quad_grid(origin, u, v, np.array([facing[0], 0, facing[1]]), cell))
    return np.concatenate(out)


def damage(scene: dict, verts: np.ndarray, faces: np.ndarray, labels: np.ndarray, rng: np.random.Generator,
           noise_sigma_m: float, hole_frac: float, drift_m: float, cell: float = CELL_M) -> trimesh.Trimesh:
    """Holes, missing ceiling, phantom patches, linear floor drift (drift_m total
    rise across the building along a random horizontal direction, applied to
    every vertex), then Gaussian vertex noise. Truth frame, meters."""
    centroids, areas = _face_geometry(verts, faces)
    keep = ~(_holes(scene, centroids, areas, labels, hole_frac, rng) | _ceiling_patches(centroids, labels, rng))
    phantom = _phantoms(scene, rng, cell)
    pv, pinv = np.unique(np.round(phantom.reshape(-1, 3), 6), axis=0, return_inverse=True)
    all_v = np.concatenate([verts, pv])
    all_f = np.concatenate([faces[keep], pinv.reshape(-1, 3) + len(verts)])
    mesh = trimesh.Trimesh(all_v, all_f, process=False)
    mesh.remove_unreferenced_vertices()
    v = mesh.vertices.copy()
    if drift_m > 0:
        angle = rng.uniform(0, 2 * np.pi)
        proj = v[:, 0] * np.cos(angle) + v[:, 2] * np.sin(angle)
        v[:, 1] += drift_m * (proj - proj.min()) / max(np.ptp(proj), 1e-9)
    v += rng.normal(0, noise_sigma_m, v.shape)
    return trimesh.Trimesh(v, mesh.faces, process=False)


# --- step 4-5: pose and write ---------------------------------------------------------


def random_pose(rng: np.random.Generator) -> np.ndarray:
    """4x4 truth->mesh: uniform random rotation (det +1) plus a translation <= 10 m."""
    m = np.eye(4)
    m[:3, :3] = Rotation.random(rng=rng).as_matrix()
    direction = rng.normal(size=3)
    m[:3, 3] = direction / np.linalg.norm(direction) * rng.uniform(0, MAX_TRANSLATION_M)
    return m


def _json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=1) + "\n")


def make_scan(truth_scene: dict, seed: int, noise_sigma_m: float = 0.015, hole_frac: float = 0.05,
              drift_m: float = 0.0, out_dir: Path = Path("data/synth/scan")) -> None:
    """Write scan.obj, scan.ply, truth.scene.json, truth_transform.json and
    scan_params.json for one single-level truth scene into out_dir.

    noise_sigma_m: Gaussian vertex noise (m). hole_frac: fraction of surface
    area removed as blobs. drift_m: total linear floor drift across the
    building (m). Raises ValueError if the furnished truth fails the validator.
    """
    rng = np.random.default_rng(seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    truth = add_furniture(truth_scene, rng)
    truth = {**truth, "meta": {**truth["meta"], "source_file": f"tools.make_synthetic_scan:{truth['meta']['name']}:seed{seed}"}}
    errors = [i for i in validate(truth) if i["severity"] == "error"]
    if errors:
        raise ValueError(f"furnished truth fails the validator: {errors}")
    _json(out_dir / "truth.scene.json", truth)
    cell = CELL_M
    verts, faces, labels = build_mesh(truth, cell)
    if len(faces) > MAX_TRIANGLES:
        # ponytail: big plans get coarser cells (edges > 0.2 m) to stay under the cap.
        cell *= math.sqrt(len(faces) / (0.95 * MAX_TRIANGLES))
        verts, faces, labels = build_mesh(truth, cell)
    mesh = damage(truth, verts, faces, labels, rng, noise_sigma_m, hole_frac, drift_m, cell)
    pose = random_pose(rng)
    mesh.apply_transform(pose)
    mesh.export(out_dir / "scan.obj", include_normals=False)
    mesh.export(out_dir / "scan.ply")
    _json(out_dir / "truth_transform.json", {"truth_to_mesh": pose.tolist()})
    _json(out_dir / "scan_params.json", {
        "layout": truth["meta"]["name"], "seed": seed, "noise_sigma_m": noise_sigma_m,
        "hole_frac": hole_frac, "drift_m": drift_m, "triangles": int(len(mesh.faces)), "cell_m": cell,
    })


# --- CLI -------------------------------------------------------------------------------


def layout_spec(name: str, seed: int) -> dict:
    """Layout spec by name: "tiny_office" or "corridor_office" (uses `seed`)."""
    if name == "tiny_office":
        return layouts.tiny_office()
    if name == "corridor_office":
        return layouts.corridor_office(seed)
    raise ValueError(f"unknown or multi-level layout {name!r}")


def bench_specs() -> list[dict]:
    """The 30 benchmark scans: 20 at (1.5 cm, 5%), 10 at (3 cm, 10%); every 5th
    is tiny_office, the rest corridor_office(seed); every 3rd has 3 cm drift."""
    specs = []
    for i in range(30):
        sigma, holes, tier = (0.015, 0.05, "a") if i < 20 else (0.03, 0.10, "b")
        layout = "tiny_office" if i % 5 == 0 else "corridor_office"
        specs.append({"name": f"{i:02d}_{layout}_{tier}", "layout": layout, "seed": i,
                      "noise_sigma_m": sigma, "hole_frac": holes, "drift_m": 0.03 if i % 3 == 2 else 0.0})
    return specs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", type=Path, help="write the 30-scan benchmark into this dir")
    ap.add_argument("--layout", default="tiny_office", help="tiny_office | corridor_office")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--sigma", type=float, default=0.015, help="vertex noise sigma (m)")
    ap.add_argument("--holes", type=float, default=0.05, help="hole area fraction")
    ap.add_argument("--drift", type=float, default=0.0, help="total floor drift (m)")
    ap.add_argument("-o", "--out", type=Path, help="output dir for a single scan")
    args = ap.parse_args(argv)
    if args.bench:
        jobs = [(s["name"], s) for s in bench_specs()]
    elif args.out:
        jobs = [(None, {"layout": args.layout, "seed": args.seed, "noise_sigma_m": args.sigma,
                        "hole_frac": args.holes, "drift_m": args.drift})]
    else:
        ap.error("give --bench DIR or -o DIR")
    for name, s in jobs:
        out = args.bench / name if name else args.out
        start = time.perf_counter()
        truth = layouts.build_scene(layout_spec(s["layout"], s["seed"]))
        make_scan(truth, s["seed"], s["noise_sigma_m"], s["hole_frac"], s["drift_m"], out)
        tris = json.loads((out / "scan_params.json").read_text())["triangles"]
        print(f"{out}: {tris} triangles, {time.perf_counter() - start:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
