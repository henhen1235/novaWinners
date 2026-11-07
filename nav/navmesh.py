"""Triangulate walkable polygons into a navmesh.

Vertex y is the level elevation in meters. The 2D triangulation lives in XZ,
which trimesh treats as XY before we swap axes.
"""

from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from geometry.polygons import flatten_polygons


def triangulate_polygon(polygon: Polygon) -> tuple[np.ndarray, np.ndarray]:
    """Return XY vertices (n, 2) and triangle indices (m, 3). XZ is passed as XY."""

    if polygon.is_empty or polygon.area < 1e-6:
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=int)
    vertices, faces = trimesh.creation.triangulate_polygon(polygon, engine="earcut")
    return np.asarray(vertices, dtype=float), np.asarray(faces, dtype=int)


def build_navmesh(
    levels: list[tuple[str, float, BaseGeometry]],
    agent_radius_m: float = 0.25,
) -> dict:
    """Triangulate each level's walkable geometry.

    ``levels`` is ``(level_id, elevation_m, walkable_geom)``.
    """

    vertices: list[list[float]] = []
    triangles: list[list[int]] = []
    triangle_level: list[str] = []
    for level_id, elevation, geom in levels:
        for polygon in flatten_polygons(geom):
            if polygon.area < 0.01:
                continue
            verts, faces = triangulate_polygon(polygon)
            if len(faces) == 0:
                continue
            offset = len(vertices)
            for x_coord, z_coord in verts:
                vertices.append([float(x_coord), float(elevation), float(z_coord)])
            for face in faces:
                a, b, c = (offset + int(face[0]), offset + int(face[1]), offset + int(face[2]))
                pa = np.array(vertices[a])
                pb = np.array(vertices[b])
                pc = np.array(vertices[c])
                area = 0.5 * np.linalg.norm(np.cross(pb - pa, pc - pa))
                if area < 1e-8:
                    continue
                triangles.append([a, b, c])
                triangle_level.append(level_id)
    return {
        "vertices": vertices,
        "triangles": triangles,
        "triangle_level": triangle_level,
        "agent_radius_m": agent_radius_m,
    }
