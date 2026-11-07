"""Turn page strokes or a raster mask into wall centerlines and door openings.

Thick dark strokes are single-line walls. Parallel dark strokes 0.10-0.40 m
apart are the two faces of a double-line wall. Coordinates in this module are
meters on the XZ plane unless a function says pixels.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from geometry.doors import openings_from_arcs
from geometry.vectorize import extend_corners, node_t_junctions, snap_segments, split_gaps
from ingest_blueprint.types import DoorOpening, PlanPage, ScaleEstimate, Segment, Stroke

THICK_WALL_M = 0.08
PAIR_MIN_M = 0.10
PAIR_MAX_M = 0.40


def detect_vector(page: PlanPage, scale: ScaleEstimate) -> tuple[list[Segment], list[DoorOpening], list[Segment]]:
    """Return wall centerlines, doors, and leftover thin segments (stair treads, distractors)."""

    walls: list[Segment] = []
    thin: list[Segment] = []
    curves: list[list[tuple[float, float]]] = []
    for stroke in page.strokes:
        points = _to_meters(stroke.points, scale.px_per_m)
        if stroke.is_curve:
            curves.append(points)
            continue
        if len(points) < 2:
            continue
        width_m = stroke.width_px / scale.px_per_m
        segment = Segment(points[0], points[-1], max(width_m, 0.05))
        if segment.length_m < 0.12:
            continue
        if not _dark(stroke.color):
            continue
        if width_m >= THICK_WALL_M:
            walls.append(Segment(segment.p1, segment.p2, max(width_m, 0.15)))
        elif segment.length_m >= 0.3:
            thin.append(segment)
    paired, leftover = _pair_double_lines(thin)
    walls.extend(paired)
    walls = node_t_junctions(snap_segments(walls))
    walls, doors = split_gaps(walls)
    walls = node_t_junctions(walls)
    doors.extend(openings_from_arcs(curves, doors))
    return walls, doors, leftover


def detect_raster(page: PlanPage, scale: ScaleEstimate) -> tuple[list[Segment], list[DoorOpening]]:
    """Classical wall mask: threshold, drop thin marks, skeletonize, Hough lines."""

    gray = cv2.cvtColor(page.raster, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 8)
    binary = _drop_small(binary, min_area_px=40)
    distance = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    positive = distance[distance > 0]
    if positive.size == 0:
        return [], []
    wall_radius = max(2.0, float(np.percentile(positive, 75)))
    keep = (distance >= max(1.6, wall_radius * 0.45)).astype(np.uint8) * 255
    kernel = max(3, int(round(wall_radius)))
    if kernel % 2 == 0:
        kernel += 1
    element = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel, kernel))
    keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, element)
    from skimage.morphology import skeletonize

    skeleton = (skeletonize(keep > 0).astype(np.uint8)) * 255
    min_len = max(12, int(scale.px_per_m * 0.4))
    gap = max(4, int(scale.px_per_m * 0.12))
    lines = cv2.HoughLinesP(skeleton, 1, np.pi / 180.0, threshold=20, minLineLength=min_len, maxLineGap=gap)
    segments: list[Segment] = []
    if lines is not None:
        for x0, y0, x1, y1 in lines.reshape(-1, 4):
            p1 = (float(x0) / scale.px_per_m, float(y0) / scale.px_per_m)
            p2 = (float(x1) / scale.px_per_m, float(y1) / scale.px_per_m)
            segments.append(Segment(p1, p2, 0.2))
    segments = extend_corners(snap_segments(segments, tolerance_m=0.2), max_extend_m=0.55)
    walls, doors = split_gaps(segments, close_gap_m=0.4)
    return node_t_junctions(walls, tol_m=0.12), doors


def deskew(page: PlanPage) -> PlanPage:
    """Rotate a raster page so the dominant dark axis is horizontal. Vector strokes are dropped."""

    gray = cv2.cvtColor(page.raster, cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    lines = cv2.HoughLines(binary, 1, np.pi / 180.0, threshold=120)
    if lines is None:
        return page
    angles = []
    for rho_theta in lines[:30]:
        theta = float(rho_theta[0][1])
        degrees = math.degrees(theta) - 90.0
        while degrees > 45.0:
            degrees -= 90.0
        while degrees < -45.0:
            degrees += 90.0
        angles.append(degrees)
    if not angles:
        return page
    # Only a consistent small skew is scan rotation. Curved walls and angled
    # wings (Tepper) give scattered angles; rotating by their median tilts the plan.
    angle = float(np.median(angles))
    agreeing = sum(1 for value in angles if abs(value - angle) <= 1.0)
    if abs(angle) < 0.2 or abs(angle) > 10.0 or agreeing < 0.6 * len(angles):
        return page
    height, width = page.raster.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(page.raster, matrix, (width, height), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
    return PlanPage(
        source_path=page.source_path,
        page_index=page.page_index,
        width_px=width,
        height_px=height,
        raster=rotated,
        strokes=[],
        texts=page.texts,
        dpi=page.dpi,
    )


def _drop_small(mask: np.ndarray, min_area_px: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    kept = np.zeros_like(mask)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= min_area_px:
            kept[labels == label] = 255
    return kept


def _to_meters(points: list[tuple[float, float]], px_per_m: float) -> list[tuple[float, float]]:
    return [(point[0] / px_per_m, point[1] / px_per_m) for point in points]


def _dark(color: tuple[float, float, float] | None) -> bool:
    if color is None:
        return True
    return max(color) < 0.45


def _pair_double_lines(segments: list[Segment]) -> tuple[list[Segment], list[Segment]]:
    used: set[int] = set()
    paired: list[Segment] = []
    for index, left in enumerate(segments):
        if index in used or left.length_m < 0.35:
            continue
        partner = None
        for other_index in range(index + 1, len(segments)):
            if other_index in used:
                continue
            right = segments[other_index]
            if right.length_m < 0.35:
                continue
            distance = _parallel_distance(left, right)
            if distance is None or not PAIR_MIN_M <= distance <= PAIR_MAX_M:
                continue
            if _overlap_ratio(left, right) < 0.55:
                continue
            partner = other_index
            break
        if partner is None:
            continue
        used.add(index)
        used.add(partner)
        paired.append(_centerline(left, segments[partner]))
    leftover = [segment for index, segment in enumerate(segments) if index not in used]
    return paired, leftover


def _parallel_distance(a: Segment, b: Segment) -> float | None:
    angle = abs(_undirected_angle(a) - _undirected_angle(b))
    angle = min(angle, 180.0 - angle)
    if angle > 5.0:
        return None
    direction = _unit(a)
    normal = (-direction[1], direction[0])
    offset_1 = (b.p1[0] - a.p1[0]) * normal[0] + (b.p1[1] - a.p1[1]) * normal[1]
    offset_2 = (b.p2[0] - a.p1[0]) * normal[0] + (b.p2[1] - a.p1[1]) * normal[1]
    if abs(offset_1 - offset_2) > 0.08:
        return None
    return abs((offset_1 + offset_2) / 2.0)


def _overlap_ratio(a: Segment, b: Segment) -> float:
    direction = _unit(a)
    origin = a.p1

    def project(segment: Segment) -> tuple[float, float]:
        values = [
            (segment.p1[0] - origin[0]) * direction[0] + (segment.p1[1] - origin[1]) * direction[1],
            (segment.p2[0] - origin[0]) * direction[0] + (segment.p2[1] - origin[1]) * direction[1],
        ]
        return min(values), max(values)

    a0, a1 = project(a)
    b0, b1 = project(b)
    overlap = min(a1, b1) - max(a0, b0)
    if overlap <= 0:
        return 0.0
    return overlap / max(min(a1 - a0, b1 - b0), 1e-6)


def _centerline(a: Segment, b: Segment) -> Segment:
    distance = _parallel_distance(a, b) or 0.2
    direction = _unit(a)
    normal = (-direction[1], direction[0])
    offset = (b.p1[0] - a.p1[0]) * normal[0] + (b.p1[1] - a.p1[1]) * normal[1]
    half = offset / 2.0

    def shift(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] + normal[0] * half, point[1] + normal[1] * half)

    return Segment(shift(a.p1), shift(a.p2), thickness_m=max(0.15, distance))


def _unit(segment: Segment) -> tuple[float, float]:
    length = segment.length_m or 1.0
    return ((segment.p2[0] - segment.p1[0]) / length, (segment.p2[1] - segment.p1[1]) / length)


def _undirected_angle(segment: Segment) -> float:
    return math.degrees(math.atan2(segment.p2[1] - segment.p1[1], segment.p2[0] - segment.p1[0])) % 180.0
