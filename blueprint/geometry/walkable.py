"""Walkable center-point area.

``walkable = (union(rooms) - union(walls) - union(obstacles)) ∪ door_bridges``,
then ``buffer(-agent_radius_m)``. Coordinates are meters in the XZ plane.

A door bridge is the door segment buffered with a flat cap by
``(wall_thickness_m + 0.05) / 2``, so its thickness equals the wall plus 5 cm
and its length equals the door width. A 0.6 m door still leaves a 0.1 m
passage after a 0.25 m erosion; a narrower door closes.
"""

from __future__ import annotations

from shapely.geometry import CAP_STYLE, JOIN_STYLE, LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geometry.polygons import clean_geometry


def door_bridge(segment: list[tuple[float, float]] | LineString, wall_thickness_m: float) -> Polygon:
    """Rectangle that spans a door opening through a wall of the given thickness."""

    line = segment if isinstance(segment, LineString) else LineString(segment)
    radius = (wall_thickness_m + 0.05) / 2.0
    return line.buffer(radius, cap_style=CAP_STYLE.flat, join_style=JOIN_STYLE.mitre)


def build_walkable(
    rooms: list[BaseGeometry],
    walls: list[BaseGeometry],
    obstacles: list[BaseGeometry],
    door_segments: list[list[tuple[float, float]] | LineString],
    wall_thickness_m: float = 0.2,
    agent_radius_m: float = 0.25,
) -> BaseGeometry:
    """Return the eroded walkable region for one level. Empty polygon if none."""

    room_union = unary_union(rooms) if rooms else Polygon()
    wall_union = unary_union(walls) if walls else Polygon()
    obstacle_union = unary_union([poly for poly in obstacles if not poly.is_empty]) if obstacles else Polygon()
    bridges = [door_bridge(segment, wall_thickness_m) for segment in door_segments]
    bridge_union = unary_union(bridges) if bridges else Polygon()
    opened = room_union.difference(wall_union).difference(obstacle_union).union(bridge_union)
    opened = clean_geometry(opened, min_area_m2=0.01)
    if opened.is_empty:
        return Polygon()
    eroded = opened.buffer(-agent_radius_m)
    return clean_geometry(eroded, min_area_m2=0.01)
