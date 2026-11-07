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
    if path.suffix.lower() in (".usd", ".usda", ".usdc", ".usdz"):
        geom = _load_usd(path)
    else:
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
