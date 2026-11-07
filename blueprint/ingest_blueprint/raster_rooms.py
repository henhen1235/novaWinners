"""Raster rooms from the white space enclosed by a sealed wall mask.

Hough lines on a skeleton stop short of corners, so polygonize never closes.
A disk dilation bridges door gaps at any wall angle, a marker watershed grows
each sealed room back into the unclosed free space, and the seams between
labels give doors. Nothing assumes axis-aligned walls; Manhattan snapping is
applied only when the recovered outlines are already orthogonal.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from shapely.geometry import Polygon

from ingest_blueprint.types import DoorOpening, PlanPage, ScaleEstimate


DOOR_M = 0.9
SIMPLIFY_M = 0.02
MANHATTAN_SHARE = 0.85
OUTSIDE = 1


def extract(page: PlanPage) -> tuple[ScaleEstimate, list[Polygon], list[DoorOpening], float]:
    """Rooms, doors and scale from a raster page, with no axis assumption.

    A disk dilation seals door gaps in walls of any orientation. Its free-space
    components (plus the page exterior) seed a marker watershed over the
    unclosed free space, so rooms keep their true outline, curves included.
    A seam between two labels that runs through free space is a door; one
    against the exterior label is an exit. Returns scale, faces (offset to
    the wall centreline), doors and the measured wall thickness in metres.
    """

    gray = cv2.cvtColor(page.raster, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 8)
    kept = _drop_small(binary, 30)
    distance = cv2.distanceTransform(kept, cv2.DIST_L2, 5)
    positive = distance[distance > 0]
    radius = float(np.percentile(positive, 70)) if positive.size else 3.0
    # Thick strokes are single-line walls. Double-line plans are thin, so keep
    # the ink mask itself when the distance transform has nothing thick.
    if radius >= 3.5:
        thick = (distance >= max(1.5, radius * 0.45)).astype(np.uint8) * 255
    else:
        thick = kept
    wall_px = _wall_thickness_px(thick, kept)
    seal, seal_px = _choose_seal(thick, wall_px)
    labels = _watershed(thick, seal)
    doors_px = _seam_doors(labels, thick, max_px=seal_px)
    widths = [width for width, _p1, _p2, _pair in doors_px if width >= seal_px * 0.4]
    if widths:
        px_per_m = float(np.median(widths)) / DOOR_M
        confidence = 0.55
    else:
        px_per_m = 100.0
        confidence = 0.3
    if not 15.0 <= px_per_m <= 500.0:
        px_per_m = 100.0
        confidence = 0.3
    wall_m = max(0.1, wall_px / px_per_m)
    faces, keep_labels = _label_faces(labels, thick, px_per_m, wall_m / 2.0)
    doors = []
    for width, p1, p2, pair in doors_px:
        width_m = width / px_per_m
        if not 0.5 <= width_m <= 2.0 or not set(pair) - {OUTSIDE} <= keep_labels:
            continue
        doors.append(
            DoorOpening((p1[0] / px_per_m, p1[1] / px_per_m), (p2[0] / px_per_m, p2[1] / px_per_m), width_m, is_exit=OUTSIDE in pair)
        )
    return ScaleEstimate(px_per_m, "door_reference", confidence), faces, doors, wall_m


def _wall_thickness_px(thick: np.ndarray, ink: np.ndarray) -> float:
    """Median wall width: twice the ink distance transform on the skeleton of the wall mask."""

    from skimage.morphology import skeletonize

    distance = cv2.distanceTransform(ink, cv2.DIST_L2, 5)
    ridge = distance[skeletonize(thick > 0)]
    return float(2.0 * np.median(ridge)) if ridge.size else 4.0


def _choose_seal(thick: np.ndarray, wall_px: float) -> tuple[np.ndarray, float]:
    """Disk dilation that encloses the most rooms.

    Dilation, not closing: a closing disk can always poke into a gap in a thin
    wall, so it never seals one. The watershed grows the shrunken seeds back
    to the true outline. Too small leaves door gaps open (rooms merge with the
    exterior); too large swallows corridors. Door width over wall thickness
    runs about 4-12 on real plans, so try that ladder and keep the size with
    the most sealed rooms (smallest on ties).
    """

    best = None
    for factor in (4, 5.5, 7, 9, 12):
        size = _odd(max(15, wall_px * factor))
        seal = cv2.dilate(thick, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
        rooms = _sealed_count(seal)
        if best is None or rooms > best[0]:
            best = (rooms, seal, float(size))
    return best[1], best[2]


def _sealed_count(seal: np.ndarray) -> int:
    count, components, stats, _ = cv2.connectedComponentsWithStats((seal == 0).astype(np.uint8), 4)
    min_area = _min_room_px(seal.shape)
    rooms = 0
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] < min_area:
            continue
        left, top = stats[index, cv2.CC_STAT_LEFT], stats[index, cv2.CC_STAT_TOP]
        right = left + stats[index, cv2.CC_STAT_WIDTH]
        bottom = top + stats[index, cv2.CC_STAT_HEIGHT]
        if left > 0 and top > 0 and right < seal.shape[1] and bottom < seal.shape[0]:
            rooms += 1
    return rooms


def _min_room_px(shape) -> int:
    return max(400, int(shape[0] * shape[1] * 2e-4))


def _watershed(thick: np.ndarray, seal: np.ndarray) -> np.ndarray:
    """Labels: OUTSIDE for the page exterior, 2.. for sealed rooms, -1 on seams, 0 never."""

    free = (seal == 0).astype(np.uint8)
    count, components, stats, _ = cv2.connectedComponentsWithStats(free, 4)
    markers = np.zeros(thick.shape, np.int32)
    next_label = OUTSIDE + 1
    min_area = _min_room_px(thick.shape)
    for index in range(1, count):
        component = components == index
        if _touches_border(component.astype(np.uint8)):
            markers[component] = OUTSIDE
        elif stats[index, cv2.CC_STAT_AREA] >= min_area:
            markers[component] = next_label
            next_label += 1
    # Seed the page margin as exterior even if the dilation sealed it shut.
    markers[0, :] = markers[-1, :] = OUTSIDE
    markers[:, 0] = markers[:, -1] = OUTSIDE
    landscape = cv2.cvtColor(thick, cv2.COLOR_GRAY2BGR)
    return cv2.watershed(landscape, markers)


def _seam_doors(labels: np.ndarray, thick: np.ndarray, max_px: float):
    """Seams through free space between two labels, as (width_px, p1, p2, (a, b))."""

    seam = (labels == -1) & (thick == 0)
    pairs: dict[tuple[int, int], list[tuple[int, int]]] = {}
    padded = np.pad(labels, 1, constant_values=-1)
    for y, x in zip(*np.nonzero(seam)):
        window = padded[y : y + 3, x : x + 3]
        found = sorted({int(v) for v in window.ravel() if v > 0})
        if len(found) == 2:
            pairs.setdefault((found[0], found[1]), []).append((x, y))
    doors = []
    for pair, points in pairs.items():
        mask = np.zeros(thick.shape, np.uint8)
        xs, ys = zip(*points)
        mask[list(ys), list(xs)] = 255
        count, parts = cv2.connectedComponents(cv2.dilate(mask, np.ones((3, 3), np.uint8)), connectivity=8)
        for index in range(1, count):
            pts = np.column_stack(np.nonzero((parts == index) & (mask > 0)))[:, ::-1].astype(np.float64)
            if len(pts) < 3:
                continue
            p1, p2 = _extent(pts)
            width = float(np.hypot(p2[0] - p1[0], p2[1] - p1[1]))
            if width <= max_px * 1.1:
                doors.append((width, p1, p2, pair))
    return doors


def _extent(points: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]]:
    center = points.mean(axis=0)
    _u, _s, vt = np.linalg.svd(points - center, full_matrices=False)
    projection = (points - center) @ vt[0]
    a = center + vt[0] * projection.min()
    b = center + vt[0] * projection.max()
    return (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))


def _label_faces(labels: np.ndarray, thick: np.ndarray, px_per_m: float, offset_m: float) -> tuple[list[Polygon], set[int]]:
    """Room faces: each watershed region's free space, offset out to the wall centreline."""

    rooms = []
    free = thick == 0
    for label in range(OUTSIDE + 1, int(labels.max()) + 1):
        component = ((labels == label) & free).astype(np.uint8)
        if component.sum() < 0.8 * px_per_m * px_per_m:
            continue
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        approx = cv2.approxPolyDP(contour, max(1.0, SIMPLIFY_M * px_per_m), True)
        rooms.append((label, [(float(pt[0][0]) / px_per_m, float(pt[0][1]) / px_per_m) for pt in approx]))
    manhattan = _is_manhattan([points for _label, points in rooms])
    faces: list[Polygon] = []
    kept: set[int] = set()
    for label, points in rooms:
        if manhattan:
            points = _axis_snap(points)
        if len(points) < 3:
            continue
        polygon = Polygon(points).buffer(offset_m, join_style=2)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.geom_type == "MultiPolygon":
            polygon = max(polygon.geoms, key=lambda part: part.area)
        if polygon.geom_type == "Polygon" and polygon.area > 1.0:
            faces.append(polygon)
            kept.add(label)
    return faces, kept


def _is_manhattan(rings: list[list[tuple[float, float]]]) -> bool:
    """True when at least MANHATTAN_SHARE of edge length lies within 5 deg of 0 or 90 deg."""

    total = aligned = 0.0
    for ring in rings:
        for start, end in zip(ring, ring[1:] + ring[:1]):
            length = math.dist(start, end)
            angle = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 90.0
            total += length
            if min(angle, 90.0 - angle) <= 5.0:
                aligned += length
    return total > 0 and aligned / total >= MANHATTAN_SHARE


def _axis_snap(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    snapped = list(points)
    for _ in range(2):
        for index, start in enumerate(snapped):
            nxt = (index + 1) % len(snapped)
            end = snapped[nxt]
            angle = abs(math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))) % 180.0
            if min(angle, 180.0 - angle) <= 12.0:
                z_value = (start[1] + end[1]) / 2.0
                snapped[index] = (start[0], z_value)
                snapped[nxt] = (end[0], z_value)
            elif abs(angle - 90.0) <= 12.0:
                x_value = (start[0] + end[0]) / 2.0
                snapped[index] = (x_value, start[1])
                snapped[nxt] = (x_value, end[1])
    return snapped


def _touches_border(mask: np.ndarray) -> bool:
    return bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())


def _drop_small(mask: np.ndarray, min_area: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    kept = np.zeros_like(mask)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= min_area:
            kept[labels == label] = 255
    return kept


def _odd(value: float) -> int:
    number = int(round(value))
    return number if number % 2 else number + 1
