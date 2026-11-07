"""Door openings from wall gaps and quarter-circle swing arcs.

Coordinates are meters in the XZ plane. A quarter-circle arc whose chord is
0.6-2.5 m becomes an opening when the wall itself was drawn solid.
"""

from __future__ import annotations

import math

from ingest_blueprint.types import DoorOpening, Segment


def openings_from_arcs(curves: list[list[tuple[float, float]]], existing: list[DoorOpening]) -> list[DoorOpening]:
    """Add doors for swing arcs that do not already sit on a detected wall gap."""

    added: list[DoorOpening] = []
    for points in curves:
        opening = _arc_opening(points)
        if opening is None:
            continue
        if any(_close(opening.midpoint, door.midpoint, 1.0) for door in existing + added):
            continue
        added.append(opening)
    return added


def cut_walls_for_doors(walls: list[Segment], doors: list[DoorOpening]) -> list[Segment]:
    """Remove the portion of a wall centerline covered by a door opening."""

    remaining = list(walls)
    for door in doors:
        next_walls: list[Segment] = []
        for wall in remaining:
            next_walls.extend(_subtract_door(wall, door))
        remaining = next_walls
    return [wall for wall in remaining if wall.length_m >= 0.05]


def _arc_opening(points: list[tuple[float, float]]) -> DoorOpening | None:
    if len(points) < 3:
        return None
    start, mid, end = points[0], points[len(points) // 2], points[-1]
    center = _circumcenter(start, mid, end)
    if center is None:
        return None
    radii = [math.dist(center, point) for point in (start, mid, end)]
    radius = sum(radii) / 3.0
    if radius < 0.5 or max(radii) - min(radii) > 0.2 * radius:
        return None
    angle = _angle(center, start, end)
    if angle is None or not 70.0 <= angle <= 110.0:
        return None
    chord = math.dist(start, end)
    # Prefer the swing radius (the leaf length) when the arc is a quarter circle.
    # The chord of a quarter circle is radius * sqrt(2), which is not the clear width.
    width = radius if 70.0 <= angle <= 110.0 else chord
    if not 0.6 <= width <= 2.5:
        return None
    # The opening is the wall line from the hinge to the closed jamb when we can
    # infer it: hinge is the center, closed position is one endpoint along the wall.
    # Use the chord only when the wall was not already gapped (caller decides).
    return DoorOpening(p1=start, p2=end, width_m=chord if 0.6 <= chord <= 2.5 else width, kind="swing")


def _subtract_door(wall: Segment, door: DoorOpening) -> list[Segment]:
    if not _collinear(wall, door.p1, door.p2):
        return [wall]
    direction = _unit(wall.p1, wall.p2)
    origin = wall.p1

    def project(point: tuple[float, float]) -> float:
        return (point[0] - origin[0]) * direction[0] + (point[1] - origin[1]) * direction[1]

    wall_a, wall_b = sorted((0.0, project(wall.p2)))
    door_a, door_b = sorted((project(door.p1), project(door.p2)))
    if door_b < wall_a + 0.05 or door_a > wall_b - 0.05:
        return [wall]
    pieces = []
    if door_a - wall_a >= 0.05:
        pieces.append(Segment(wall.p1, _at(origin, direction, door_a), wall.thickness_m))
    if wall_b - door_b >= 0.05:
        pieces.append(Segment(_at(origin, direction, door_b), wall.p2, wall.thickness_m))
    return pieces


def _collinear(wall: Segment, p1, p2, tol: float = 0.12) -> bool:
    direction = _unit(wall.p1, wall.p2)
    normal = (-direction[1], direction[0])
    for point in (p1, p2):
        offset = abs((point[0] - wall.p1[0]) * normal[0] + (point[1] - wall.p1[1]) * normal[1])
        if offset > tol:
            return False
    door_dir = _unit(p1, p2)
    return abs(direction[0] * door_dir[0] + direction[1] * door_dir[1]) > 0.95


def _unit(p1, p2) -> tuple[float, float]:
    length = math.dist(p1, p2) or 1.0
    return ((p2[0] - p1[0]) / length, (p2[1] - p1[1]) / length)


def _at(origin, direction, distance: float) -> tuple[float, float]:
    return (origin[0] + direction[0] * distance, origin[1] + direction[1] * distance)


def _close(a, b, tol: float) -> bool:
    return math.dist(a, b) <= tol


def _circumcenter(a, b, c):
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-6:
        return None
    a2 = a[0] * a[0] + a[1] * a[1]
    b2 = b[0] * b[0] + b[1] * b[1]
    c2 = c[0] * c[0] + c[1] * c[1]
    ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d
    uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d
    return (ux, uy)


def _angle(center, start, end) -> float | None:
    ax, ay = start[0] - center[0], start[1] - center[1]
    bx, by = end[0] - center[0], end[1] - center[1]
    na = math.hypot(ax, ay)
    nb = math.hypot(bx, by)
    if na < 1e-6 or nb < 1e-6:
        return None
    cosine = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(cosine))
