"""Raster rooms from the white space enclosed by a sealed wall mask.

Hough lines on a skeleton stop short of corners, so polygonize never closes.
A morphological close bridges door gaps, flood-fill drops the page exterior,
and each remaining component is a room. Door width is the ink gap along that
bridge, not the inflated closing kernel.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from shapely.geometry import Polygon

from ingest_blueprint.types import DoorOpening, PlanPage, ScaleEstimate


def extract(page: PlanPage) -> tuple[ScaleEstimate, list[Polygon], list[DoorOpening]]:
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
    small_k = _odd(max(15, radius * 3))
    big_k = _odd(max(101, radius * 14))
    small = cv2.morphologyEx(thick, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (small_k, small_k)))
    big = cv2.morphologyEx(thick, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (big_k, big_k)))
    gaps = _door_gaps(thick, big, small)
    gaps = [gap for gap in gaps if 70 <= gap[0] <= 150]
    gaps = _merge_gaps(gaps, 50.0)
    if gaps:
        px_per_m = float(np.median([gap[0] for gap in gaps])) / 0.9
        confidence = 0.55
    else:
        px_per_m = 100.0
        confidence = 0.3
    if not 15.0 <= px_per_m <= 500.0:
        px_per_m = 100.0
        confidence = 0.3
    faces = _rooms(big, px_per_m)
    doors = []
    for length, p1, p2 in gaps:
        width_m = length / px_per_m
        if not 0.6 <= width_m <= 3.0:
            continue
        doors.append(
            DoorOpening((p1[0] / px_per_m, p1[1] / px_per_m), (p2[0] / px_per_m, p2[1] / px_per_m), width_m)
        )
    return ScaleEstimate(px_per_m, "door_reference", confidence), faces, doors


def _rooms(closed: np.ndarray, px_per_m: float) -> list[Polygon]:
    interior = (closed == 0).astype(np.uint8)
    flood = interior.copy()
    mask = np.zeros((interior.shape[0] + 2, interior.shape[1] + 2), np.uint8)
    cv2.floodFill(flood, mask, (2, 2), 0)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(flood, 8)
    faces: list[Polygon] = []
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] < 2500:
            continue
        component = (labels == index).astype(np.uint8)
        if _touches_border(component):
            continue
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        approx = cv2.approxPolyDP(contour, 2.5, True)
        points = _axis_snap([(float(point[0][0]) / px_per_m, float(point[0][1]) / px_per_m) for point in approx])
        if len(points) < 3:
            continue
        polygon = Polygon(points).buffer(0.10)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.geom_type == "MultiPolygon":
            polygon = max(polygon.geoms, key=lambda part: part.area)
        if polygon.geom_type == "Polygon" and polygon.area > 1.0:
            faces.append(polygon)
    return faces


def _door_gaps(thick: np.ndarray, big: np.ndarray, small: np.ndarray) -> list[tuple[float, tuple[float, float], tuple[float, float]]]:
    bridge = ((big > 0) & (small == 0)).astype(np.uint8)
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(bridge, 8)
    height, width = thick.shape
    gaps = []
    for index in range(1, count):
        box_w = int(stats[index, cv2.CC_STAT_WIDTH])
        box_h = int(stats[index, cv2.CC_STAT_HEIGHT])
        short, long = sorted((box_w, box_h))
        if long < 40 or long > 220 or short > 42:
            continue
        x = int(stats[index, cv2.CC_STAT_LEFT])
        y = int(stats[index, cv2.CC_STAT_TOP])
        if box_w >= box_h:
            row = min(height - 1, y + box_h // 2)
            length = _longest_zero(thick[row, x : x + box_w])
            p1 = (float(x), float(row))
            p2 = (float(x + box_w), float(row))
        else:
            col = min(width - 1, x + box_w // 2)
            length = _longest_zero(thick[y : y + box_h, col])
            p1 = (float(col), float(y))
            p2 = (float(col), float(y + box_h))
        if length >= 40:
            gaps.append((float(length), p1, p2))
    return gaps


def _merge_gaps(gaps, radius_px: float):
    used: set[int] = set()
    merged = []
    for index, gap in enumerate(gaps):
        if index in used:
            continue
        group = [index]
        mid = ((gap[1][0] + gap[2][0]) / 2.0, (gap[1][1] + gap[2][1]) / 2.0)
        for other_index, other in enumerate(gaps):
            if other_index <= index or other_index in used:
                continue
            other_mid = ((other[1][0] + other[2][0]) / 2.0, (other[1][1] + other[2][1]) / 2.0)
            if abs(other_mid[0] - mid[0]) <= radius_px and abs(other_mid[1] - mid[1]) <= radius_px:
                used.add(other_index)
                group.append(other_index)
        used.add(index)
        merged.append(max((gaps[item] for item in group), key=lambda item: item[0]))
    return merged


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


def _longest_zero(values) -> int:
    best = current = 0
    for value in values:
        if int(value) == 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


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
