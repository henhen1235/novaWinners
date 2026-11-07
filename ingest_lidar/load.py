"""Load a scan (mesh or point cloud) in the file's own frame.

Units are taken as meters (ARKit, Polycam and RoomPlan exports are metric);
USDZ files are scaled by their metersPerUnit. No axis conversion happens here
except USD Z-up stages: orient.py finds "up" from the geometry itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh


@dataclass(frozen=True)
class Scan:
    """Raw scan geometry. vertices: (n, 3) float64 meters; faces: (m, 3) int or None."""

    vertices: np.ndarray
    faces: np.ndarray | None
    colors: np.ndarray | None  # (n, 3) uint8 per vertex, or None
    info: dict = field(default_factory=dict)
    # ARKit ARMeshClassification per face (0 none, 1 wall, 2 floor, 3 ceiling,
    # 4 table, 5 seat, 6 window, 7 door) when the file carries it, else None.
    face_classes: np.ndarray | None = None


ARKIT_PLY_COMMENT = b"comment rehearsal arkit mesh v1"
ARKIT_CLASSES = ("none", "wall", "floor", "ceiling", "table", "seat", "window", "door")


def _load_arkit_ply(path: Path) -> Scan | None:
    """Read RoomScanner's Mesh-mode PLY (binary LE, float xyz, int triangles + uchar class).

    Returns None if the file is not in that exact layout, so the generic loader runs.
    Frame: ARKit world, gravity aligned, Y up, meters.
    """
    raw = path.read_bytes()
    end = raw.find(b"end_header\n")
    if end < 0 or ARKIT_PLY_COMMENT not in raw[:end]:
        return None
    header = raw[:end].decode("ascii", "replace").splitlines()
    counts = {ln.split()[1]: int(ln.split()[2]) for ln in header if ln.startswith("element ")}
    body = raw[end + len(b"end_header\n"):]
    nv, nf = counts.get("vertex", 0), counts.get("face", 0)
    v = np.frombuffer(body, dtype="<f4", count=nv * 3).reshape(nv, 3).astype(np.float64)
    face_dt = np.dtype([("n", "u1"), ("idx", "<i4", (3,)), ("cls", "u1")])  # 14 bytes, packed
    faces = np.frombuffer(body, dtype=face_dt, count=nf, offset=nv * 12)
    if nf and (faces["n"] != 3).any():
        raise ValueError(f"{path.name}: Mesh-mode PLY must contain triangles only")
    f = faces["idx"].astype(np.int64)
    if nf and (f.max() >= nv or f.min() < 0):
        raise ValueError(f"{path.name}: face index out of range")
    cls = faces["cls"].astype(np.uint8)
    info = _info(path, v, f, False, False)
    info["arkit_classes"] = {name: int((cls == k).sum()) for k, name in enumerate(ARKIT_CLASSES)}
    return Scan(v, f, None, info, cls)


def _load_usd(path: Path) -> trimesh.Trimesh:
    """Flatten every UsdGeom.Mesh in a USD/USDZ stage into one mesh in meters."""
    from pxr import Usd, UsdGeom  # usd-core

    stage = Usd.Stage.Open(str(path))
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    cache = UsdGeom.XformCache()
    verts, faces, offset = [], [], 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        pts = np.asarray(mesh.GetPointsAttr().Get() or [], dtype=np.float64)
        counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get() or [], dtype=np.int64)
        idx = np.asarray(mesh.GetFaceVertexIndicesAttr().Get() or [], dtype=np.int64)
        if len(pts) == 0 or len(counts) == 0:
            continue
        m = np.asarray(cache.GetLocalToWorldTransform(prim), dtype=np.float64)  # row-vector convention
        pts = np.c_[pts, np.ones(len(pts))] @ m
        tris, start = [], 0
        for c in counts:  # fan-triangulate polygons
            tris.extend([idx[start], idx[start + k], idx[start + k + 1]] for k in range(1, c - 1))
            start += c
        verts.append(pts[:, :3] * mpu)
        faces.append(np.asarray(tris, dtype=np.int64).reshape(-1, 3) + offset)
        offset += len(pts)
    if not verts:
        raise ValueError(f"no meshes found in {path}")
    v = np.vstack(verts)
    if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z:  # proper rotation: (x, y, z) -> (x, z, -y)
        v = np.c_[v[:, 0], v[:, 2], -v[:, 1]]
    return trimesh.Trimesh(v, np.vstack(faces), process=False)


def load_scan(path: Path) -> Scan:
    """Load OBJ/PLY/GLB/GLTF/STL/USD(Z) as a mesh, or a face-less PLY as a point cloud."""
    path = Path(path)
    if path.suffix.lower() == ".ply":
        arkit = _load_arkit_ply(path)
        if arkit is not None:
            return arkit
    if path.suffix.lower() in (".usd", ".usda", ".usdc", ".usdz"):
        geom = _load_usd(path)
    else:
        geom = trimesh.load(path, process=False)
        if not isinstance(geom, trimesh.PointCloud):  # force="mesh" turns a face-less PLY into 0 vertices
            geom = trimesh.load(path, force="mesh", process=False)
    if isinstance(geom, trimesh.PointCloud) or len(getattr(geom, "faces", [])) == 0:
        pts = np.asarray(geom.vertices, dtype=np.float64)
        colors = None
        if getattr(geom, "colors", None) is not None and len(geom.colors) == len(pts):
            colors = np.asarray(geom.colors)[:, :3].astype(np.uint8)
        return Scan(pts, None, colors, _info(path, pts, None, colors is not None, False))
    has_texture = isinstance(geom.visual, trimesh.visual.TextureVisuals)
    colors = None
    try:
        vc = geom.visual.to_color().vertex_colors if has_texture else geom.visual.vertex_colors
        if vc is not None and len(vc) == len(geom.vertices) and np.ptp(vc[:, :3]) > 0:
            colors = np.asarray(vc)[:, :3].astype(np.uint8)
    except Exception:  # ponytail: color is cosmetic, geometry still loads without it
        colors = None
    v = np.asarray(geom.vertices, dtype=np.float64)
    f = np.asarray(geom.faces, dtype=np.int64)
    return Scan(v, f, colors, _info(path, v, f, colors is not None, has_texture))


def _info(path: Path, v: np.ndarray, f: np.ndarray | None, colors: bool, texture: bool) -> dict:
    return {
        "file": path.name,
        "kind": "mesh" if f is not None else "point_cloud",
        "vertices": int(len(v)),
        "triangles": int(len(f)) if f is not None else 0,
        "bbox_min": v.min(0).round(3).tolist(),
        "bbox_max": v.max(0).round(3).tolist(),
        "has_vertex_colors": colors,
        "has_texture": texture,
        "units_assumed": "meters",
    }
