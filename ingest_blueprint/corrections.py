"""Apply pixel-space corrections after auto-detection.

Points are source-image pixels at 200 DPI, origin top-left, y down.
``set_scale`` is handled before this module. Thickness and door width are meters.
"""

from __future__ import annotations

import math

from shapely.geometry import Polygon

from ingest_blueprint.types import DoorOpening, Segment


def manual_scale(corrections: dict | None) -> tuple[tuple[float, float], tuple[float, float], float] | None:
    if not corrections:
        return None
    for op in corrections.get("operations", []):
        if op.get("op") == "set_scale":
            return (tuple(op["p1"]), tuple(op["p2"]), float(op["meters"]))
    return None


def scale_from_manual(p1, p2, meters: float) -> float:
    pixels = math.dist(p1, p2)
    if pixels < 1.0 or meters <= 0:
        raise ValueError("set_scale needs two distinct pixels and a positive distance")
    return pixels / meters


def apply_corrections(
    walls: list[Segment],
    doors: list[DoorOpening],
    corrections: dict | None,
    px_per_m: float,
) -> tuple[list[Segment], list[DoorOpening], list[tuple[str, tuple[float, float], float]], list[Polygon], list[Polygon]]:
    """Return walls, doors, extra labels (text, anchor meters, size), stair polygons, obstacles."""

    labels: list[tuple[str, tuple[float, float], float]] = []
    stairs: list[Polygon] = []
    obstacles: list[Polygon] = []
    if not corrections:
        return walls, doors, labels, stairs, obstacles

    def meters(point) -> tuple[float, float]:
        return (float(point[0]) / px_per_m, float(point[1]) / px_per_m)

    for op in corrections.get("operations", []):
        kind = op.get("op")
        if kind == "set_scale":
            continue
        if kind == "add_wall_line":
            walls.append(Segment(meters(op["p1"]), meters(op["p2"]), float(op.get("thickness", 0.2))))
        elif kind == "remove_wall_at":
            center = meters(op["point"])
            radius = float(op.get("radius", 8.0)) / px_per_m
            walls = [wall for wall in walls if _segment_distance(center, wall) > radius]
        elif kind == "add_door":
            center = meters(op["point"])
            width = float(op.get("width", 0.9))
            walls, created = _punch_door(walls, center, width)
            if created is not None:
                doors.append(created)
        elif kind == "remove_door_at":
            center = meters(op["point"])
            kept = []
            for door in doors:
                if math.dist(door.midpoint, center) <= 0.6:
                    walls.append(Segment(door.p1, door.p2, 0.2))
                else:
                    kept.append(door)
            doors = kept
        elif kind == "set_exit":
            center = meters(op["point"])
            if doors:
                nearest = min(doors, key=lambda door: math.dist(door.midpoint, center))
                nearest.is_exit = True
                nearest.label = str(op.get("label", "Exit"))
        elif kind == "label_room":
            labels.append((str(op.get("name", "Room")), meters(op["point"]), 14.0))
        elif kind == "add_stair":
            ring = [meters(point) for point in op["polygon"]]
            if len(ring) >= 3:
                stairs.append(Polygon(ring))
        elif kind == "add_obstacle":
            ring = [meters(point) for point in op["polygon"]]
            if len(ring) >= 3:
                obstacles.append(Polygon(ring))
    return walls, doors, labels, stairs, obstacles


def _segment_distance(point: tuple[float, float], wall: Segment) -> float:
    from shapely.geometry import LineString, Point

    return float(LineString([wall.p1, wall.p2]).distance(Point(point)))


def _punch_door(walls: list[Segment], center: tuple[float, float], width: float) -> tuple[list[Segment], DoorOpening | None]:
    if not walls:
        return walls, None
    nearest = min(walls, key=lambda wall: _segment_distance(center, wall))
    if _segment_distance(center, nearest) > 0.4:
        return walls, None
    direction = _unit(nearest)
    origin = nearest.p1

    def project(point: tuple[float, float]) -> float:
        return (point[0] - origin[0]) * direction[0] + (point[1] - origin[1]) * direction[1]

    cursor = project(center)
    half = width / 2.0
    start = project(nearest.p1)
    end = project(nearest.p2)
    low, high = min(start, end), max(start, end)
    gap_a = max(low, cursor - half)
    gap_b = min(high, cursor + half)
    if gap_b - gap_a < 0.5:
        return walls, None
    pieces = [wall for wall in walls if wall is not nearest]
    if gap_a - low >= 0.05:
        pieces.append(Segment(nearest.p1 if start <= end else _at(origin, direction, high), _at(origin, direction, gap_a), nearest.thickness_m))
        # Rebuild from the true low end.
        pieces.pop()
    rebuilt = [wall for wall in walls if wall is not nearest]
    if gap_a - low >= 0.05:
        rebuilt.append(Segment(_at(origin, direction, low), _at(origin, direction, gap_a), nearest.thickness_m))
    if high - gap_b >= 0.05:
        rebuilt.append(Segment(_at(origin, direction, gap_b), _at(origin, direction, high), nearest.thickness_m))
    opening = DoorOpening(_at(origin, direction, gap_a), _at(origin, direction, gap_b), gap_b - gap_a)
    return rebuilt, opening


def _unit(segment: Segment) -> tuple[float, float]:
    length = segment.length_m or 1.0
    return ((segment.p2[0] - segment.p1[0]) / length, (segment.p2[1] - segment.p1[1]) / length)


def _at(origin, direction, distance: float) -> tuple[float, float]:
    return (origin[0] + direction[0] * distance, origin[1] + direction[1] * distance)
