"""Extrude plan polygons into a display glTF.

``trimesh`` extrudes a polygon that lives in XY along Z. Floor-plan (x, z) is
passed in as XY, then the mesh axes are swapped so extruded height becomes Y.
"""

from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon, box

from geometry.polygons import flatten_polygons


def extrude_xz(polygon: Polygon, height_m: float, elevation_m: float, color: list[int]) -> trimesh.Trimesh | None:
    if polygon.is_empty or polygon.area < 1e-4 or height_m <= 0:
        return None
    mesh = trimesh.creation.extrude_polygon(polygon, height=height_m)
    vertices = mesh.vertices.copy()
    mesh.vertices = np.column_stack([vertices[:, 0], vertices[:, 2] + elevation_m, vertices[:, 1]])
    # Vertex colors avoid trimesh's face-to-vertex conversion, which imports
    # scipy.sparse. That import is broken on some macOS SciPy wheels.
    color_row = np.array(color, dtype=np.uint8)
    mesh.visual.vertex_colors = np.tile(color_row, (len(mesh.vertices), 1))
    return mesh


def lintel_polygon(segment: list[list[float]], thickness_m: float) -> Polygon:
    line = LineString(segment)
    return line.buffer(max(thickness_m, 0.05) / 2.0, cap_style=2)


def build_display(scene: dict) -> trimesh.Scene:
    """Named nodes: walls, floors, doors, exits, stairs, obstacles."""

    levels = {level["id"]: level for level in scene["levels"]}
    groups: dict[str, list[trimesh.Trimesh]] = {
        "walls": [],
        "floors": [],
        "doors": [],
        "exits": [],
        "stairs": [],
        "obstacles": [],
    }
    for wall in scene["walls"]:
        elevation = levels[wall["level"]]["elevation_m"]
        mesh = extrude_xz(Polygon(wall["polygon"]), wall["height_m"], elevation, [190, 190, 190, 255])
        if mesh is not None:
            groups["walls"].append(mesh)
    for room in scene["rooms"]:
        elevation = levels[room["level"]]["elevation_m"]
        mesh = extrude_xz(Polygon(room["polygon"]), 0.04, elevation, [210, 205, 196, 255])
        if mesh is not None:
            groups["floors"].append(mesh)
    exit_doors = {exit_["door_id"] for exit_ in scene["exits"]}
    for door in scene["doors"]:
        elevation = levels[door["level"]]["elevation_m"]
        ceiling = levels[door["level"]]["ceiling_height_m"]
        footprint = lintel_polygon(door["segment"], 0.2)
        lintel = extrude_xz(footprint, max(0.2, ceiling - 2.1), elevation + 2.1, [170, 170, 170, 255])
        if lintel is not None:
            groups["doors"].append(lintel)
        if door["id"] in exit_doors:
            marker = extrude_xz(footprint, 0.08, elevation + 0.02, [40, 170, 80, 255])
            if marker is not None:
                groups["exits"].append(marker)
    for stair in scene["stairs"]:
        elevation = levels[stair["from_level"]]["elevation_m"]
        mesh = extrude_xz(Polygon(stair["polygon"]), 0.12, elevation + 0.05, [90, 120, 170, 255])
        if mesh is not None:
            groups["stairs"].append(mesh)
    for obstacle in scene["obstacles"]:
        elevation = levels[obstacle["level"]]["elevation_m"]
        mesh = extrude_xz(Polygon(obstacle["polygon"]), obstacle["height_m"], elevation, [140, 100, 70, 255])
        if mesh is not None:
            groups["obstacles"].append(mesh)
    display = trimesh.Scene()
    for name, meshes in groups.items():
        if not meshes:
            # Keep the node present so the viewer can toggle it.
            placeholder = extrude_xz(box(0, 0, 0.01, 0.01), 0.01, 0.0, [0, 0, 0, 0])
            if placeholder is not None:
                display.add_geometry(placeholder, geom_name=name, node_name=name)
            continue
        combined = trimesh.util.concatenate(meshes)
        display.add_geometry(combined, geom_name=name, node_name=name)
    return display


def build_navmesh_debug(navmesh: dict) -> trimesh.Trimesh | None:
    vertices = np.array(navmesh["vertices"], dtype=float)
    if len(vertices) == 0 or not navmesh["triangles"]:
        return None
    vertices = vertices.copy()
    vertices[:, 1] += 0.05
    mesh = trimesh.Trimesh(vertices=vertices, faces=np.array(navmesh["triangles"], dtype=int), process=False)
    color_row = np.array([70, 130, 200, 180], dtype=np.uint8)
    mesh.visual.vertex_colors = np.tile(color_row, (len(mesh.vertices), 1))
    return mesh


def wall_polygons_from_segments(segments) -> list[Polygon]:
    polygons = []
    for segment in segments:
        line = LineString([segment.p1, segment.p2])
        if line.length < 0.05:
            continue
        polygons.append(line.buffer(segment.thickness_m / 2.0, cap_style=2))
    return [poly for poly in flatten_polygons(__import__("shapely").ops.unary_union(polygons))] if polygons else []
