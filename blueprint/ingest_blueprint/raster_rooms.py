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
MAX_ASPECT = 25.0  # longer and thinner than any corridor: title-block bands, slivers


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
    # Thick strokes are single-line walls. Double-line plans are thin, so keep
    # the ink mask itself when the distance transform has nothing thick. The
    # 90th percentile (not the median) sees 0.2 m walls drawn at 60 px/m,
    # where arcs and text outnumber wall pixels.
    radius = float(np.percentile(positive, 90)) if positive.size else 3.0
    double_line = radius < 3.0
    thick = kept if double_line else (distance >= max(1.5, radius * 0.35)).astype(np.uint8) * 255
    wall_px = _wall_thickness_px(thick, kept)
    if not double_line:
        thick = cv2.bitwise_or(thick, _straight_thin_lines(kept, thick, wall_px))
    seal, seal_px = _choose_seal(thick, wall_px)
    labels = _merge_open_seams(_watershed(thick, seal, wall_px), thick, seal_px)
    if double_line:
        # The watershed splits each gap between a wall's two hairlines at its
        # centre, so faces already end on the centreline; the wall is that gap.
        wall_px = _double_line_gap_px(labels, thick, seal_px) + 1.0
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
    wall_m = min(0.5, max(0.1, wall_px / px_per_m))
    faces, keep_labels = _label_faces(labels, thick, px_per_m, 0.0 if double_line else wall_m / 2.0)
    doors = []
    for width, p1, p2, pair in doors_px:
        if not double_line and _glazed(kept, thick, p1, p2, wall_px):
            continue
        width_m = width / px_per_m
        if width_m < 0.5 or (width_m > 2.0 and OUTSIDE not in pair) or not set(pair) - {OUTSIDE} <= keep_labels:
            continue
        doors.append(
            DoorOpening((p1[0] / px_per_m, p1[1] / px_per_m), (p2[0] / px_per_m, p2[1] / px_per_m), width_m, is_exit=OUTSIDE in pair)
        )
    return ScaleEstimate(px_per_m, "door_reference", confidence), faces, doors, wall_m


def _straight_thin_lines(ink: np.ndarray, thick: np.ndarray, wall_px: float) -> np.ndarray:
    """Thin ink that is long and straight (glazing, thin partitions) as wall pixels.

    Windows are drawn as thin outlines between solid wall ends; left out, every
    window is a gap in the envelope and its room leaks outside. Text, swing
    arcs and dashes are short or round, and free-floating lines do not touch a
    wall, so they stay out.
    """

    rim = max(3, int(wall_px))
    walls = cv2.dilate(thick, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (rim, rim)))
    thin = ((ink > 0) & (walls == 0)).astype(np.uint8)
    near_wall = cv2.dilate(walls, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rim + 1, 2 * rim + 1))) > 0
    count, parts, stats, _ = cv2.connectedComponentsWithStats(thin, connectivity=8)
    keep = np.zeros(count, np.uint8)
    for index in range(1, count):
        left, top, width, height, _area = (int(v) for v in stats[index])
        if max(width, height) < 4 * wall_px:
            continue
        ys, xs = np.nonzero(parts[top : top + height, left : left + width] == index)
        (_cx, _cy), (side_a, side_b), _angle = cv2.minAreaRect(np.column_stack((xs, ys)).astype(np.float32))
        long_side, short_side = max(side_a, side_b), max(min(side_a, side_b), 1.0)
        # Glazing spans between wall ends; scale bars, dimension lines and hatching float free.
        touches = near_wall[ys + top, xs + left].any()
        if touches and long_side >= 4 * wall_px and long_side / short_side >= 4:
            keep[index] = 255
    return keep[parts]


def _glazed(ink: np.ndarray, thick: np.ndarray, p1, p2, wall_px: float) -> bool:
    """True when thin ink spans the whole gap inside the wall line: a window, not a doorway.

    Glazing is drawn as thin lines across the full opening, within the wall
    thickness. A swing arc or an "EXIT" label covers only part of that strip.
    """

    length = math.dist(p1, p2)
    if length < 2:
        return False
    mask = np.zeros(ink.shape, np.uint8)
    cv2.line(mask, (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1])), 255, max(3, int(wall_px)))
    # The wall mask is the ink eroded, so its rim is "thin" ink too; grow it back first.
    rim = max(3, int(wall_px))
    walls = cv2.dilate(thick, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (rim, rim)))
    ys, xs = np.nonzero((ink > 0) & (walls == 0) & (mask > 0))
    if xs.size == 0:
        return False
    direction = (np.array(p2) - np.array(p1)) / length
    along = (np.column_stack((xs, ys)) - np.array(p1)) @ direction
    covered = np.unique(np.clip(along, 0, length - 1).astype(int)).size
    return covered / length >= 0.9


def _wall_thickness_px(thick: np.ndarray, ink: np.ndarray) -> float:
    """Median wall width: twice the ink distance transform on the skeleton of the wall mask."""

    from skimage.morphology import skeletonize

    distance = cv2.distanceTransform(ink, cv2.DIST_L2, 5)
    ridge = distance[skeletonize(thick > 0)]
    return float(2.0 * np.median(ridge)) if ridge.size else 4.0


def _double_line_gap_px(labels: np.ndarray, thick: np.ndarray, seal_px: float) -> float:
    """Twice the median ink clearance on narrow seams: the spacing of double-line walls.

    Only seams squeezed between ink count; open seams across a room say
    nothing about walls. Single-line boundaries leave none, so 2 px.
    """

    clearance = cv2.distanceTransform((thick == 0).astype(np.uint8), cv2.DIST_L2, 5)
    values = clearance[(labels == -1) & (thick == 0)]
    values = values[values < seal_px / 4.0]
    return float(2.0 * np.median(values)) if values.size else 2.0


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


def _watershed(thick: np.ndarray, seal: np.ndarray, wall_px: float) -> np.ndarray:
    """Labels: OUTSIDE for the page exterior, 2.. for sealed rooms, -1 on seams, 0 never.

    Free space inside the building footprint is never exterior, even when a
    glazed or open stretch of envelope (wider than any door) lets it leak out
    to the page edge; it is cut along the footprint outline and seeded as rooms.
    """

    free = ((seal == 0) & (_footprint_outline(thick, wall_px) == 0)).astype(np.uint8)
    count, components, stats, _ = cv2.connectedComponentsWithStats(free, 4)
    lookup = np.zeros(count, np.int32)
    next_label = OUTSIDE + 1
    min_area = _min_room_px(thick.shape)
    height, width = thick.shape
    for index in range(1, count):
        left, top, box_w, box_h, area = (int(v) for v in stats[index])
        if left == 0 or top == 0 or left + box_w >= width or top + box_h >= height:
            lookup[index] = OUTSIDE
        elif area >= min_area:
            lookup[index] = next_label
            next_label += 1
    markers = lookup[components]
    # Seed the page margin as exterior even if the dilation sealed it shut.
    markers[0, :] = markers[-1, :] = OUTSIDE
    markers[:, 0] = markers[:, -1] = OUTSIDE
    landscape = cv2.cvtColor(thick, cv2.COLOR_GRAY2BGR)
    return cv2.watershed(landscape, markers)


def _footprint_outline(thick: np.ndarray, wall_px: float) -> np.ndarray:
    """Outer outline of the building: wall ink closed by a disk, holes ignored.

    The disk is 2% of the page. Where the envelope is already closed the
    outline lies on the ink and changes nothing. ponytail: a page fraction;
    size it in metres once scale is known (12 wall thicknesses was tried and
    cost exits on the synthetic plans).
    """

    size = _odd(max(15, 0.02 * max(thick.shape)))
    closed = cv2.morphologyEx(thick, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    outline = np.zeros_like(thick)
    cv2.drawContours(outline, contours, -1, 255, 3)
    return outline


def _neighbour_labels(labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Largest and smallest positive label in each 3x3 window (smallest is 1e7 where none)."""

    big = np.float32(1e7)
    kernel = np.ones((3, 3), np.uint8)
    high = cv2.dilate(np.where(labels > 0, labels, 0).astype(np.float32), kernel)
    low = -cv2.dilate(np.where(labels > 0, -labels.astype(np.float32), -big), kernel)
    return high, low


def _merge_open_seams(labels: np.ndarray, thick: np.ndarray, seal_px: float) -> np.ndarray:
    """Join labels whose seam is longer than a door and runs through open floor.

    Big open areas (atria, lobbies, corridors) are seeded as several pieces
    where the dilation pinches them; the seam between two such pieces is an
    opening wider than any door, not a wall, so they are one space.
    """

    high, low = _neighbour_labels(labels)
    seam = (labels == -1) & (thick == 0) & (high > OUTSIDE) & (low > OUTSIDE) & (low < 1e7) & (high != low)
    clearance = cv2.distanceTransform((thick == 0).astype(np.uint8), cv2.DIST_L2, 5)
    count, parts, stats, _ = cv2.connectedComponentsWithStats(seam.astype(np.uint8), connectivity=8)
    parent = np.arange(int(labels.max()) + 1)

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    merged = False
    for index in range(1, count):
        left, top, width, height, _area = (int(v) for v in stats[index])
        if np.hypot(width, height) <= seal_px * 1.1:
            continue
        ys, xs = np.nonzero(parts[top : top + height, left : left + width] == index)
        if np.median(clearance[ys + top, xs + left]) < seal_px / 4.0:
            continue
        a, b = find(int(low[ys[0] + top, xs[0] + left])), find(int(high[ys[0] + top, xs[0] + left]))
        if a != b:
            parent[a] = b
            merged = True
    if not merged:
        return labels
    lookup = np.array([find(node) for node in range(len(parent))], np.int32)
    relabeled = np.where(labels > 0, lookup[np.maximum(labels, 0)], labels)
    high, low = _neighbour_labels(relabeled)
    inner = (relabeled == -1) & (high == low) & (high > 0)
    relabeled[inner] = high[inner].astype(np.int32)
    return relabeled


def _seam_doors(labels: np.ndarray, thick: np.ndarray, max_px: float):
    """Seams through free space between two labels, as (width_px, p1, p2, (a, b)).

    A seam no longer than the seal is a door. A longer seam to the exterior that
    runs through open floor is an opening in the envelope (glazing, an entrance
    the drawing leaves open) and is kept as an exit.
    """

    clearance = cv2.distanceTransform((thick == 0).astype(np.uint8), cv2.DIST_L2, 5)
    high, low = _neighbour_labels(labels)
    seam = (labels == -1) & (thick == 0) & (high > 0) & (low < 1e7) & (high != low)
    count, parts, stats, _ = cv2.connectedComponentsWithStats(seam.astype(np.uint8), connectivity=8)
    doors = []
    for index in range(1, count):
        left, top, width, height, area = (int(v) for v in stats[index])
        if area < 3:
            continue
        ys, xs = np.nonzero(parts[top : top + height, left : left + width] == index)
        if np.hypot(width, height) > max_px * 1.1 and (
            int(low[ys[0] + top, xs[0] + left]) != OUTSIDE or np.median(clearance[ys + top, xs + left]) < max_px / 4.0
        ):
            continue
        pts = np.column_stack((xs + left, ys + top)).astype(np.float64)
        pair_low = low[ys + top, xs + left].astype(np.int64)
        pair_high = high[ys + top, xs + left].astype(np.int64)
        keys, counts = np.unique(pair_low * 1_000_000 + pair_high, return_counts=True)
        key = int(keys[np.argmax(counts)])
        p1, p2 = _extent(pts)
        doors.append((float(np.hypot(p2[0] - p1[0], p2[1] - p1[1])), p1, p2, (key // 1_000_000, key % 1_000_000)))
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

    from scipy.ndimage import find_objects

    rooms = []
    room_labels = np.where((thick == 0) & (labels > OUTSIDE), labels, 0)
    for label, window in enumerate(find_objects(room_labels), start=1):
        if window is None:
            continue
        component = (room_labels[window] == label).astype(np.uint8)
        # Pixel threshold, same as the seeds: px_per_m may still be a guess here.
        if component.sum() < _min_room_px(labels.shape):
            continue
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE, offset=(window[1].start, window[0].start))
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
        polygon = Polygon(points).buffer(offset_m, join_style=2) if offset_m > 0 else Polygon(points).buffer(0)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.geom_type == "MultiPolygon":
            polygon = max(polygon.geoms, key=lambda part: part.area)
        if polygon.geom_type == "Polygon" and not polygon.is_empty and _aspect(polygon) <= MAX_ASPECT:
            faces.append(polygon)
            kept.add(label)
    return faces, kept


def _aspect(polygon: Polygon) -> float:
    corners = list(polygon.minimum_rotated_rectangle.exterior.coords)
    sides = sorted((math.dist(corners[0], corners[1]), math.dist(corners[1], corners[2])))
    return sides[1] / max(sides[0], 1e-6)


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


def _drop_small(mask: np.ndarray, min_area: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.where(stats[:, cv2.CC_STAT_AREA] >= min_area, 255, 0).astype(mask.dtype)
    keep[0] = 0
    return keep[labels]


def _odd(value: float) -> int:
    number = int(round(value))
    return number if number % 2 else number + 1
