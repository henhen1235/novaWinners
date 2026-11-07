"""Clean wall centerlines in meters on the XZ plane.

Snap radius 0.10 m, gap close under 0.15 m, Manhattan snap within 5 degrees
of the axes. Door gaps of 0.6-2.5 m are returned separately and are not closed.
"""

from __future__ import annotations

import math

from ingest_blueprint.types import DoorOpening, Segment

SNAP_M = 0.10
CLOSE_GAP_M = 0.15
MIN_DOOR_M = 0.6
MAX_DOOR_M = 2.5
MANHATTAN_DEG = 5.0


def _length(p1, p2) -> float:
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def _angle_deg(p1, p2) -> float:
    return math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0])) % 180.0


def manhattan(p1: tuple[float, float], p2: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
    """Snap a segment to horizontal or vertical when it is already within 5 degrees."""

    angle = _angle_deg(p1, p2)
    if min(angle, 180.0 - angle) <= MANHATTAN_DEG:
        z_value = (p1[1] + p2[1]) / 2.0
        return (p1[0], z_value), (p2[0], z_value)
    if abs(angle - 90.0) <= MANHATTAN_DEG:
        x_value = (p1[0] + p2[0]) / 2.0
        return (x_value, p1[1]), (x_value, p2[1])
    return p1, p2


def snap_segments(segments: list[Segment], tolerance_m: float = SNAP_M) -> list[Segment]:
    """Move endpoints that lie within ``tolerance_m`` onto their cluster mean."""

    points: list[tuple[float, float]] = []
    for segment in segments:
        points.append(segment.p1)
        points.append(segment.p2)
    parent = list(range(len(points)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, point_i in enumerate(points):
        for j in range(i + 1, len(points)):
            if _length(point_i, points[j]) <= tolerance_m:
                parent[find(i)] = find(j)
    groups: dict[int, list[tuple[float, float]]] = {}
    for index, point in enumerate(points):
        groups.setdefault(find(index), []).append(point)
    means = {
        key: (
            sum(point[0] for point in group) / len(group),
            sum(point[1] for point in group) / len(group),
        )
        for key, group in groups.items()
    }
    snapped: list[Segment] = []
    seen: set[tuple] = set()
    for index, segment in enumerate(segments):
        p1 = means[find(2 * index)]
        p2 = means[find(2 * index + 1)]
        p1, p2 = manhattan(p1, p2)
        if _length(p1, p2) < 0.05:
            continue
        key = tuple(sorted(((round(p1[0], 3), round(p1[1], 3)), (round(p2[0], 3), round(p2[1], 3)))))
        if key in seen:
            continue
        seen.add(key)
        snapped.append(Segment(p1, p2, segment.thickness_m))
    return snapped


def node_t_junctions(segments: list[Segment], tol_m: float = 0.04) -> list[Segment]:
    """Project endpoints onto nearby wall bodies and split those walls.

    Double-line centerlines miss a crossing wall by a centimeter or two.
    Polygonize will not close the room until the endpoint sits on the segment.
    """

    endpoint_snap: dict[tuple[int, int], tuple[float, float]] = {}
    split_points: dict[int, list[tuple[float, float]]] = {}
    for index, segment in enumerate(segments):
        for which, point in ((0, segment.p1), (1, segment.p2)):
            best_distance = tol_m
            best: tuple[int, tuple[float, float]] | None = None
            for other_index, other in enumerate(segments):
                if other_index == index:
                    continue
                distance, projected, t_param = _project(point, other.p1, other.p2)
                if distance >= best_distance or t_param <= 0.001 or t_param >= 0.999:
                    continue
                if _length(projected, other.p1) <= 0.02 or _length(projected, other.p2) <= 0.02:
                    continue
                best_distance = distance
                best = (other_index, projected)
            if best is None:
                continue
            endpoint_snap[(index, which)] = best[1]
            split_points.setdefault(best[0], []).append(best[1])
    output: list[Segment] = []
    for index, segment in enumerate(segments):
        updated = Segment(
            endpoint_snap.get((index, 0), segment.p1),
            endpoint_snap.get((index, 1), segment.p2),
            segment.thickness_m,
        )
        output.extend(_split_segment(updated, split_points.get(index, [])))
    return output


def extend_corners(segments: list[Segment], max_extend_m: float = 0.55) -> list[Segment]:
    """Extend endpoints to a nearby intersection with a non-parallel wall.

    Raster Hough lines stop short of corners. A door gap is collinear, so this
    does not bridge it; only a crossing wall pulls an endpoint outward.
    """

    updated = list(segments)
    for i in range(len(updated)):
        for j in range(i + 1, len(updated)):
            hit = _segment_intersection(updated[i], updated[j])
            if hit is None:
                continue
            updated[i] = _extend_toward(updated[i], hit, max_extend_m)
            updated[j] = _extend_toward(updated[j], hit, max_extend_m)
    return [segment for segment in updated if segment.length_m >= 0.05]


def split_gaps(segments: list[Segment], close_gap_m: float = CLOSE_GAP_M) -> tuple[list[Segment], list[DoorOpening]]:
    """Close tiny collinear gaps and record door-sized gaps.

    Segments are grouped onto the same infinite line when their angle matches
    within 3 degrees and their lateral offset is under 0.08 m.
    """

    unused = set(range(len(segments)))
    groups: list[list[int]] = []
    for index in range(len(segments)):
        if index not in unused:
            continue
        group = [index]
        unused.remove(index)
        for other in list(unused):
            if _same_line(segments[index], segments[other]):
                group.append(other)
                unused.remove(other)
        groups.append(group)

    walls: list[Segment] = []
    doors: list[DoorOpening] = []
    for group in groups:
        direction = _direction(segments[group[0]])
        origin = segments[group[0]].p1

        def project(point: tuple[float, float]) -> float:
            return (point[0] - origin[0]) * direction[0] + (point[1] - origin[1]) * direction[1]

        intervals = []
        for index in group:
            segment = segments[index]
            a = project(segment.p1)
            b = project(segment.p2)
            intervals.append((min(a, b), max(a, b), segment.thickness_m, segment))
        # Identical overlapping strokes are common in CAD exports. Sorting the
        # segment object itself raises TypeError, so only the numbers decide order.
        intervals.sort(key=lambda item: (item[0], item[1], item[2]))
        merged = [intervals[0]]
        for start, end, thickness, segment in intervals[1:]:
            prev_start, prev_end, prev_thickness, prev_segment = merged[-1]
            gap = start - prev_end
            if gap <= close_gap_m:
                merged[-1] = (prev_start, max(prev_end, end), max(prev_thickness, thickness), prev_segment)
                continue
            if MIN_DOOR_M <= gap <= MAX_DOOR_M:
                opening_start = _at(origin, direction, prev_end)
                opening_end = _at(origin, direction, start)
                # A corridor between two T-junctions is collinear too. A real
                # door jamb does not sit on a crossing wall.
                if not _hits_other_wall(opening_start, segments, group, 0.15) and not _hits_other_wall(
                    opening_end, segments, group, 0.15
                ):
                    doors.append(DoorOpening(p1=opening_start, p2=opening_end, width_m=gap))
            merged.append((start, end, thickness, segment))
        for start, end, thickness, segment in merged:
            if end - start < 0.05:
                continue
            walls.append(Segment(_at(origin, direction, start), _at(origin, direction, end), thickness))
    return walls, doors


def _segment_intersection(a: Segment, b: Segment) -> tuple[float, float] | None:
    """Intersection of the infinite lines, or None when they are nearly parallel."""

    ax, az = a.p2[0] - a.p1[0], a.p2[1] - a.p1[1]
    bx, bz = b.p2[0] - b.p1[0], b.p2[1] - b.p1[1]
    det = ax * bz - az * bx
    if abs(det) < 1e-8:
        return None
    dx = b.p1[0] - a.p1[0]
    dz = b.p1[1] - a.p1[1]
    t_param = (dx * bz - dz * bx) / det
    return (a.p1[0] + t_param * ax, a.p1[1] + t_param * az)


def _extend_toward(segment: Segment, hit: tuple[float, float], max_extend_m: float) -> Segment:
    length = segment.length_m or 1.0
    direction = ((segment.p2[0] - segment.p1[0]) / length, (segment.p2[1] - segment.p1[1]) / length)
    along = (hit[0] - segment.p1[0]) * direction[0] + (hit[1] - segment.p1[1]) * direction[1]
    if along < 0.0 and -along <= max_extend_m:
        return Segment(hit, segment.p2, segment.thickness_m)
    if along > length and along - length <= max_extend_m:
        return Segment(segment.p1, hit, segment.thickness_m)
    return segment


def _project(point, start, end) -> tuple[float, tuple[float, float], float]:
    vx = end[0] - start[0]
    vz = end[1] - start[1]
    length_sq = vx * vx + vz * vz
    if length_sq < 1e-12:
        return _length(point, start), start, 0.0
    t_param = ((point[0] - start[0]) * vx + (point[1] - start[1]) * vz) / length_sq
    t_clamped = max(0.0, min(1.0, t_param))
    projected = (start[0] + t_clamped * vx, start[1] + t_clamped * vz)
    return _length(point, projected), projected, t_clamped


def _split_segment(segment: Segment, points: list[tuple[float, float]]) -> list[Segment]:
    if _length(segment.p1, segment.p2) < 0.05:
        return []
    cuts: list[tuple[float, tuple[float, float]]] = []
    for point in points:
        _distance, projected, t_param = _project(point, segment.p1, segment.p2)
        if 0.02 < t_param < 0.98:
            cuts.append((t_param, projected))
    cuts.sort()
    if not cuts:
        return [segment]
    bounds = [segment.p1] + [point for _t, point in cuts] + [segment.p2]
    pieces: list[Segment] = []
    for start, end in zip(bounds, bounds[1:]):
        if _length(start, end) < 0.05:
            continue
        pieces.append(Segment(start, end, segment.thickness_m))
    return pieces


def _direction(segment: Segment) -> tuple[float, float]:
    length = segment.length_m or 1.0
    return ((segment.p2[0] - segment.p1[0]) / length, (segment.p2[1] - segment.p1[1]) / length)


def _at(origin, direction, distance: float) -> tuple[float, float]:
    return (origin[0] + direction[0] * distance, origin[1] + direction[1] * distance)


def _hits_other_wall(point: tuple[float, float], segments: list[Segment], group: list[int], tol_m: float = 0.15) -> bool:
    """True when ``point`` lies on a wall that is not part of this collinear run."""

    group_set = set(group)
    for index, segment in enumerate(segments):
        if index in group_set:
            continue
        if _point_segment_distance(point, segment.p1, segment.p2) <= tol_m:
            return True
    return False


def _point_segment_distance(point, start, end) -> float:
    vx = end[0] - start[0]
    vz = end[1] - start[1]
    length_sq = vx * vx + vz * vz
    if length_sq < 1e-12:
        return _length(point, start)
    t = ((point[0] - start[0]) * vx + (point[1] - start[1]) * vz) / length_sq
    t = max(0.0, min(1.0, t))
    nearest = (start[0] + t * vx, start[1] + t * vz)
    return _length(point, nearest)


def _same_line(a: Segment, b: Segment) -> bool:
    angle = abs(_angle_deg(a.p1, a.p2) - _angle_deg(b.p1, b.p2))
    angle = min(angle, 180.0 - angle)
    if angle > 3.0:
        return False
    direction = _direction(a)
    normal = (-direction[1], direction[0])
    offset_b = (b.p1[0] - a.p1[0]) * normal[0] + (b.p1[1] - a.p1[1]) * normal[1]
    offset_b2 = (b.p2[0] - a.p1[0]) * normal[0] + (b.p2[1] - a.p1[1]) * normal[1]
    return abs(offset_b) < 0.08 and abs(offset_b2) < 0.08
