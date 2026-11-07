"""Estimate pixels per meter.

The worked door-reference case: a 30 px opening assumed to be 0.9 m is 33.333 px/m.
Scale-bar and dimension text are tried first because they carry an explicit length.
"""

from __future__ import annotations

import re
from statistics import median

from ingest_blueprint.types import PlanPage, ScaleEstimate, Stroke

_METER = re.compile(r"(\d+(?:\.\d+)?)\s*m\b", re.IGNORECASE)
_MILLIMETER = re.compile(r"(?<!\d)(\d{3,5})(?!\d)")
_FEET = re.compile(r"(\d+)\s*'\s*-?\s*(\d+(?:\.\d+)?)\s*\"")


def px_per_m_from_door_widths(widths_px: list[float], assumed_m: float = 0.9) -> float:
    """Convert detected door widths in pixels to px/m. ``assumed_m`` is the reference door."""

    if not widths_px:
        raise ValueError("no door widths")
    return float(median(widths_px)) / assumed_m


def estimate_scale(page: PlanPage, manual: ScaleEstimate | None = None) -> ScaleEstimate:
    if manual is not None:
        return manual
    bar = _scale_bar(page)
    if bar is not None:
        return bar
    dimension = _dimension(page)
    if dimension is not None:
        return dimension
    door = _door_reference(page)
    if door is not None:
        return door
    return ScaleEstimate(px_per_m=50.0, method="door_reference", confidence=0.2)


def _line_strokes(page: PlanPage) -> list[Stroke]:
    return [stroke for stroke in page.strokes if not stroke.is_curve and len(stroke.points) >= 2]


def _length(points: list[tuple[float, float]]) -> float:
    (x0, y0), (x1, y1) = points[0], points[-1]
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5


def _near_lines(page: PlanPage, center: tuple[float, float], radius_px: float) -> list[Stroke]:
    """Lines whose body passes within ``radius_px`` of a text anchor, not just their midpoint."""

    found = []
    for stroke in _line_strokes(page):
        if _point_segment_distance(center, stroke.points[0], stroke.points[-1]) <= radius_px:
            found.append(stroke)
    return found


def _point_segment_distance(point, start, end) -> float:
    vx = end[0] - start[0]
    vy = end[1] - start[1]
    length_sq = vx * vx + vy * vy
    if length_sq < 1e-9:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    t = ((point[0] - start[0]) * vx + (point[1] - start[1]) * vy) / length_sq
    t = max(0.0, min(1.0, t))
    nearest = (start[0] + t * vx, start[1] + t * vy)
    return ((point[0] - nearest[0]) ** 2 + (point[1] - nearest[1]) ** 2) ** 0.5


def _plausible(px_per_m: float) -> bool:
    return 15.0 <= px_per_m <= 500.0


def _scale_bar(page: PlanPage) -> ScaleEstimate | None:
    best: tuple[float, float] | None = None
    for span in page.texts:
        numbers = [float(value) for value in _METER.findall(span.text)]
        meters = max(numbers) if numbers else 0.0
        if meters < 0.5:
            continue
        candidates = _near_lines(page, span.center, 80.0)
        for stroke in candidates:
            # Walls are thick. A scale bar is a thin stroke sitting next to the label.
            if stroke.width_px > 6.0:
                continue
            length = _length(stroke.points)
            if length < 20.0:
                continue
            px_per_m = length / meters
            if not _plausible(px_per_m):
                continue
            distance = _point_segment_distance(span.center, stroke.points[0], stroke.points[-1])
            if best is None or distance < best[0]:
                best = (distance, px_per_m)
    if best is None:
        return None
    return ScaleEstimate(px_per_m=best[1], method="scale_bar", confidence=0.95)


def _dimension(page: PlanPage) -> ScaleEstimate | None:
    best: tuple[float, float] | None = None
    for span in page.texts:
        meters = _parse_dimension_meters(span.text)
        if meters is None or meters < 0.5:
            continue
        for stroke in _near_lines(page, span.center, 70.0):
            if stroke.width_px > 6.0:
                continue
            length = _length(stroke.points)
            if length < 20.0:
                continue
            px_per_m = length / meters
            if not _plausible(px_per_m):
                continue
            distance = _point_segment_distance(span.center, stroke.points[0], stroke.points[-1])
            if best is None or distance < best[0]:
                best = (distance, px_per_m)
    if best is None:
        return None
    return ScaleEstimate(px_per_m=best[1], method="dimension_text", confidence=0.8)


def _parse_dimension_meters(text: str) -> float | None:
    feet = _FEET.search(text)
    if feet:
        return int(feet.group(1)) * 0.3048 + float(feet.group(2)) * 0.0254
    match = _MILLIMETER.search(text)
    if not match:
        return None
    value = int(match.group(1))
    if value < 500:
        return None
    return value / 1000.0


def _door_reference(page: PlanPage) -> ScaleEstimate | None:
    radii = []
    for stroke in page.strokes:
        if not stroke.is_curve:
            continue
        radius = _quarter_radius_px(stroke.points)
        if radius is not None and 12.0 <= radius <= 450.0:
            radii.append(radius)
    if not radii:
        return None
    return ScaleEstimate(
        px_per_m=px_per_m_from_door_widths(radii, 0.9),
        method="door_reference",
        confidence=0.55,
    )


def _quarter_radius_px(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 3:
        return None
    start, mid, end = points[0], points[len(points) // 2], points[-1]
    center = _circumcenter(start, mid, end)
    if center is None:
        return None
    radii = [_dist(center, point) for point in (start, mid, end)]
    radius = sum(radii) / 3.0
    if radius < 1.0 or max(radii) - min(radii) > 0.2 * radius:
        return None
    angle = _angle(center, start, end)
    if angle is None or not 70.0 <= angle <= 110.0:
        return None
    return radius


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


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _angle(center, start, end) -> float | None:
    import math

    ax, ay = start[0] - center[0], start[1] - center[1]
    bx, by = end[0] - center[0], end[1] - center[1]
    na = math.hypot(ax, ay)
    nb = math.hypot(bx, by)
    if na < 1e-6 or nb < 1e-6:
        return None
    cosine = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(cosine))
