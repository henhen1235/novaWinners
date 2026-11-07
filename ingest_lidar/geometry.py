"""Shared 2D/3D geometry helpers for the scene contract.

Frame and units everywhere in this module: meters, right-handed, Y up. The
floor plan is the XZ plane, so a 2D point is [x, z] and a 3D point is
[x, y, z]. Contract coordinates are rounded to 1 mm (3 decimals).

Used by the hand-written fixtures (tools.layouts), the synthetic scan
generator, the RoomPlan converter and the LiDAR pipeline. See DECISIONS.md D6.
"""

from __future__ import annotations

import numpy as np
import shapely
import trimesh
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

PRECISION_M = 0.001
MIN_PART_AREA_M2 = 0.05
MIN_TRIANGLE_AREA_M2 = 1e-6
DOOR_GAP_MARGIN_M = 0.01


def _r(value: float) -> float:
    """Round to 1 mm; `+ 0.0` turns -0.0 into 0.0 so JSON output is stable."""
    return round(float(value), 3) + 0.0


def _ring_coords(coords) -> list[list[float]]:
    """Shapely ring coords to a contract ring: 1 mm rounding, no closing point."""
    points = [[_r(x), _r(z)] for x, z in list(coords)[:-1]]
    # Rounding can merge neighbours; drop consecutive duplicates.
    return [p for i, p in enumerate(points) if p != points[i - 1]] or points


def ring(poly: Polygon) -> list[list[float]]:
    """Exterior ring of `poly` as [[x, z], ...] in meters, 1 mm rounded, unclosed."""
    return _ring_coords(poly.exterior.coords)


def _polygons(geom: BaseGeometry) -> list[Polygon]:
    """All non-empty Polygon parts of any geometry (lines and points dropped)."""
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if hasattr(geom, "geoms"):
        return [p for g in geom.geoms for p in _polygons(g)]
    return []


def door_gap_box(segment: list[list[float]], wall_thickness_m: float) -> Polygon:
    """Rectangle covering a door opening through its wall.

    `segment` is the door's [[x, z], [x, z]] centerline in meters. The box spans
    the segment lengthwise and extends wall_thickness_m / 2 + 1 cm to each side
    across the wall, so it overlaps the room interiors on both sides.
    """
    p0, p1 = np.asarray(segment[0], float), np.asarray(segment[1], float)
    direction = p1 - p0
    length = float(np.hypot(*direction))
    if length <= 0:
        raise ValueError(f"door segment has zero length: {segment}")
    normal = np.array([-direction[1], direction[0]]) / length
    offset = normal * (wall_thickness_m / 2 + DOOR_GAP_MARGIN_M)
    return Polygon([p0 + offset, p1 + offset, p1 - offset, p0 - offset])


def walkable_from_parts(
    free: list[Polygon], blockers: list[Polygon], agent_radius_m: float = 0.25
) -> MultiPolygon:
    """Walkable area (XZ, meters) for one level, per DECISIONS.md D6.

    union(free) minus union(blockers), eroded by the agent radius with
    buffer(-r), snapped to a 1 mm grid, parts under 0.05 m^2 dropped. Always
    returns a valid MultiPolygon, possibly empty.
    """
    area = unary_union(free) if free else Polygon()
    if blockers:
        area = area.difference(unary_union(blockers))
    eroded = area.buffer(-agent_radius_m)
    snapped = shapely.set_precision(eroded, PRECISION_M)
    # set_precision's default mode guarantees valid output, so any subset of
    # its parts is still a valid MultiPolygon.
    return MultiPolygon([p for p in _polygons(snapped) if p.area >= MIN_PART_AREA_M2])


def walkable_to_contract(mp: MultiPolygon) -> dict:
    """Contract `walkable[level]` value: {"polygons": [{"exterior", "holes"}]}."""
    return {
        "polygons": [
            {"exterior": ring(p), "holes": [_ring_coords(h.coords) for h in p.interiors]}
            for p in mp.geoms
        ]
    }


def _rounded_polygon(poly: Polygon) -> Polygon:
    """`poly` rebuilt from its contract (1 mm) coordinates, so the navmesh
    vertices are exactly the walkable vertices written to scene.json."""
    return Polygon(ring(poly), [_ring_coords(h.coords) for h in poly.interiors])


def _triangulate(poly: Polygon) -> tuple[np.ndarray, np.ndarray]:
    """Earcut triangulation: (n, 2) XZ vertices, (m, 3) faces, degenerates dropped,
    faces wound so their 3D normal points +Y."""
    verts, faces = trimesh.creation.triangulate_polygon(poly, engine="earcut")
    verts, faces = np.asarray(verts, float).reshape(-1, 2), np.asarray(faces, np.int64).reshape(-1, 3)
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    signed = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    faces = faces[np.abs(signed) >= MIN_TRIANGLE_AREA_M2]
    signed = signed[np.abs(signed) >= MIN_TRIANGLE_AREA_M2]
    # In (x, z) a positive signed area means the 3D normal points -Y; flip those.
    faces[signed > 0] = faces[signed > 0][:, [0, 2, 1]]
    return verts, faces


def navmesh_from_walkable(
    walkable: dict[str, MultiPolygon],
    elevations: dict[str, float],
    agent_radius_m: float = 0.25,
) -> dict:
    """Contract navmesh from per-level walkable areas.

    Each polygon is earcut-triangulated in XZ; vertices become [x, elevation, z]
    (meters, 1 mm rounded, shared between triangles with equal positions).
    Triangles with XZ area < 1e-6 m^2 are dropped. Levels are processed in
    sorted order and polygons in the given order, so output is deterministic.
    """
    index: dict[tuple[float, float, float], int] = {}
    triangles: list[list[int]] = []
    triangle_level: list[str] = []
    for level in sorted(walkable):
        y = _r(elevations[level])
        for poly in walkable[level].geoms:
            verts, faces = _triangulate(_rounded_polygon(poly))
            keys = [(_r(x), y, _r(z)) for x, z in verts]
            ids = [index.setdefault(k, len(index)) for k in keys]
            triangles.extend([ids[i] for i in face] for face in faces.tolist())
            triangle_level.extend([level] * len(faces))
    return {
        "vertices": [list(k) for k in index],
        "triangles": triangles,
        "triangle_level": triangle_level,
        "agent_radius_m": agent_radius_m,
    }


def extrude_xz(polygon: Polygon, y0: float, height: float) -> trimesh.Trimesh:
    """Closed prism (meters) whose world-XZ footprint equals `polygon` (x, z).

    Spans y0 to y0 + height with outward normals. The polygon is extruded in
    XY as (x, -z) along +Z, then rotated -90 deg about X, which maps
    (x, -z, w) to (x, w, z). A rotation, unlike an axis swap, keeps the
    footprint unmirrored and the normals outward.
    """
    flipped = shapely.transform(polygon, lambda c: c * np.array([1.0, -1.0]))
    mesh = trimesh.creation.extrude_polygon(flipped, height)
    # Exact -90 deg rotation about X: (x, y, z) -> (x, z, -y), det +1.
    rotate = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], float)
    mesh.apply_transform(rotate)
    mesh.apply_translation([0.0, y0, 0.0])
    return mesh


def exit_reachable_parts(
    walkable: MultiPolygon, exit_points: list[list[float]], tol_m: float = 1.0
) -> MultiPolygon:
    """Parts of `walkable` (XZ, meters) within tol_m of at least one [x, z] exit point."""
    points = [Point(p) for p in exit_points]
    return MultiPolygon([part for part in walkable.geoms if any(part.distance(pt) <= tol_m for pt in points)])
