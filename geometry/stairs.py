"""Stair symbols: 6 or more parallel treads, or a room already typed as a stairwell.

Single-level enclosed stairs are exits (``leads_to=stairwell``) and do not
create a link. Multi-level stairs become ``links`` with ``cost_multiplier`` 2.
"""

from __future__ import annotations

import math

from shapely.geometry import Point, Polygon

from ingest_blueprint.types import Segment


def tread_clusters(thin_segments: list[Segment]) -> list[Polygon]:
    """Bounding rectangles of evenly spaced parallel tread lines."""

    unused = set(range(len(thin_segments)))
    clusters: list[list[Segment]] = []
    for index, seed in enumerate(thin_segments):
        if index not in unused or seed.length_m < 0.4:
            continue
        group = [seed]
        unused.remove(index)
        for other_index in list(unused):
            other = thin_segments[other_index]
            if _parallel(seed, other) and _bundle_distance(seed, other) < 3.0:
                group.append(other)
                unused.remove(other_index)
        if len(group) >= 6 and _even_spacing(group):
            clusters.append(group)
    polygons = []
    for group in clusters:
        xs = [point[0] for segment in group for point in (segment.p1, segment.p2)]
        zs = [point[1] for segment in group for point in (segment.p1, segment.p2)]
        polygons.append(Polygon([(min(xs), min(zs)), (max(xs), min(zs)), (max(xs), max(zs)), (min(xs), max(zs))]))
    return polygons


def stair_room_indexes(faces: list, stair_polys: list[Polygon], texts: list[tuple[str, tuple[float, float]]]) -> set[int]:
    indexes: set[int] = set()
    for index, face in enumerate(faces):
        for stair in stair_polys:
            if face.intersects(stair) and face.intersection(stair).area > 0.4 * stair.area:
                indexes.add(index)
        for text, anchor in texts:
            token = text.strip().lower()
            if token in {"up", "dn", "down", "stair", "stairs"} or "stair" in token:
                if face.covers(Point(anchor)):
                    indexes.add(index)
    return indexes


def align_levels(level_stairs: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    """Translation to apply to each level so stair centroids match level 0.

    The first level's shift is (0, 0). Later levels move by the mean offset of
    their stair centroids against the first level. Translation only.
    """

    if not level_stairs:
        return []
    shifts = [(0.0, 0.0)]
    base = level_stairs[0]
    for stairs in level_stairs[1:]:
        if not base or not stairs:
            shifts.append((0.0, 0.0))
            continue
        pairs = []
        for point in stairs:
            nearest = min(base, key=lambda other: math.dist(point, other))
            pairs.append((nearest[0] - point[0], nearest[1] - point[1]))
        shifts.append((sum(item[0] for item in pairs) / len(pairs), sum(item[1] for item in pairs) / len(pairs)))
    return shifts


def _parallel(a: Segment, b: Segment) -> bool:
    angle = abs(_angle(a) - _angle(b))
    angle = min(angle, 180.0 - angle)
    return angle < 8.0


def _angle(segment: Segment) -> float:
    return math.degrees(math.atan2(segment.p2[1] - segment.p1[1], segment.p2[0] - segment.p1[0])) % 180.0


def _bundle_distance(a: Segment, b: Segment) -> float:
    mid_a = ((a.p1[0] + a.p2[0]) / 2.0, (a.p1[1] + a.p2[1]) / 2.0)
    mid_b = ((b.p1[0] + b.p2[0]) / 2.0, (b.p1[1] + b.p2[1]) / 2.0)
    return math.dist(mid_a, mid_b)


def _even_spacing(group: list[Segment]) -> bool:
    direction = _normal(group[0])
    offsets = sorted(
        (segment.p1[0] * direction[0] + segment.p1[1] * direction[1]) for segment in group
    )
    gaps = [offsets[index + 1] - offsets[index] for index in range(len(offsets) - 1)]
    gaps = [gap for gap in gaps if gap > 0.05]
    if len(gaps) < 5:
        return False
    mean = sum(gaps) / len(gaps)
    if mean < 0.15 or mean > 0.5:
        return False
    variance = sum((gap - mean) ** 2 for gap in gaps) / len(gaps)
    return variance**0.5 < 0.35 * mean


def _normal(segment: Segment) -> tuple[float, float]:
    length = segment.length_m or 1.0
    return (-(segment.p2[1] - segment.p1[1]) / length, (segment.p2[0] - segment.p1[0]) / length)
