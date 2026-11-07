"""display.glb (decimated scan without ceiling + clean walls) and navmesh_debug.glb.

All geometry is in the scene frame (meters, Y up), which is also glTF's frame.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon

from .geometry import extrude_xz
from .load import Scan
from .orient import apply

MAX_FACES = 500_000
CUT_HEIGHT_M = 2.5


def scan_mesh(scan: Scan, matrix: np.ndarray) -> trimesh.Trimesh | None:
    """Scan in the scene frame, faces above CUT_HEIGHT_M dropped, decimated to MAX_FACES."""
    if scan.faces is None:
        return None
    v = apply(matrix, scan.vertices)
    f = scan.faces[(v[scan.faces][:, :, 1] < CUT_HEIGHT_M).all(1)]
    colors = scan.colors if scan.colors is not None else np.full((len(v), 3), 180, np.uint8)
    mesh = trimesh.Trimesh(v, f, vertex_colors=np.c_[colors, np.full(len(v), 255, np.uint8)], process=False)
    mesh.remove_unreferenced_vertices()
    if len(mesh.faces) > MAX_FACES:
        import fast_simplification

        nv, nf = fast_simplification.simplify(mesh.vertices, mesh.faces, 1 - MAX_FACES / len(mesh.faces))
        # ponytail: nearest-vertex color transfer; fine for display
        _, idx = trimesh.proximity.ProximityQuery(mesh).vertex(nv)
        mesh = trimesh.Trimesh(nv, nf, vertex_colors=mesh.visual.vertex_colors[idx], process=False)
    return mesh


def write_display(path: Path, scan: Scan, matrix: np.ndarray, walls: list[dict]) -> None:
    """Scene with node "scan" and node group "walls_clean" (one child node per wall)."""
    scene = trimesh.Scene()
    mesh = scan_mesh(scan, matrix)
    if mesh is not None:
        scene.add_geometry(mesh, node_name="scan", geom_name="scan")
    if walls:
        scene.graph.update(frame_to="walls_clean", frame_from=scene.graph.base_frame)
        for w in walls:
            m = extrude_xz(Polygon(w["polygon"]), 0.0, w["height_m"])
            m.visual.face_colors = [235, 235, 235, 255]
            scene.add_geometry(m, node_name=w["id"], geom_name=w["id"], parent_node_name="walls_clean")
    scene.export(path)


def write_navmesh_debug(path: Path, navmesh: dict, doors: list[dict]) -> None:
    """Navmesh lifted 2 cm (green), plus a post at each door (red = exit, blue = interior)."""
    scene = trimesh.Scene()
    v = np.asarray(navmesh["vertices"], float)
    t = np.asarray(navmesh["triangles"], int)
    if len(t):
        nm = trimesh.Trimesh(v + [0, 0.02, 0], t, process=False)
        nm.visual.face_colors = [60, 180, 90, 255]
        scene.add_geometry(nm, node_name="navmesh")
    for d in doors:
        c = np.mean(d["segment"], axis=0)
        post = trimesh.creation.box((0.15, 2.0, 0.15))
        post.apply_translation([c[0], 1.0, c[1]])
        post.visual.face_colors = [220, 40, 40, 255] if d["is_exit"] else [60, 90, 220, 255]
        scene.add_geometry(post, node_name=d["id"])
    if not scene.geometry:  # degrade gracefully: an empty navmesh still gets a file
        scene.add_geometry(trimesh.creation.box((0.01, 0.01, 0.01)), node_name="empty")
    scene.export(path)
