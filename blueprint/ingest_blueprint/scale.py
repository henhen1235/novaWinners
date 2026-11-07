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


_AREA = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*(sf|sq\.?\s*ft|ft2|m2|m\u00b2|sqm)\s*$", re.IGNORECASE)
SQFT_M2 = 0.09290304


def area_label_ratio(faces, texts_m, wall_m: float) -> float | None:
    """Linear scale correction from room area tags ("395 SF", "12.5 m2").

    For each face holding exactly one area tag, compare the face's net area
    (inset by half a wall) with the tag. Returns the median length ratio
    true/current (multiply coordinates by it), or None with fewer than three
    tags or when the tags disagree by more than 25%.
    """

    from shapely.geometry import Point

    tags = []
    for text, anchor in texts_m:
        match = _AREA.match(text)
        if match:
            value = float(match.group(1).replace(",", ""))
            metric = match.group(2).lower() in {"m2", "m\u00b2", "sqm"}
            tags.append((value if metric else value * SQFT_M2, Point(anchor)))
    ratios = []
    for face in faces:
        inside = [m2 for m2, point in tags if face.contains(point)]
        net = face.buffer(-wall_m / 2.0, join_style=2).area
        if len(inside) == 1 and inside[0] > 1.0 and net > 0:
            ratios.append((inside[0] / net) ** 0.5)
    if len(ratios) < 3:
        return None
    ratios.sort()
    middle = ratios[len(ratios) // 2]
    spread = ratios[3 * len(ratios) // 4] / ratios[len(ratios) // 4]
    return middle if spread <= 1.25 else None


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


def _line_array(page: PlanPage) -> "np.ndarray":
    """Thin straight strokes as an (N, 5) array: x0, y0, x1, y1, width_px."""

    import numpy as np

    rows = [
        (*stroke.points[0], *stroke.points[-1], stroke.width_px)
        for stroke in _line_strokes(page)
        if stroke.width_px <= 6.0 and _length(stroke.points) >= 20.0
    ]
    return np.asarray(rows, np.float64).reshape(-1, 5)


def _closest_line(lines, center: tuple[float, float], radius_px: float) -> tuple[float, float] | None:
    """(distance, length) of the thin line passing closest to ``center`` within ``radius_px``."""

    import numpy as np

    if not len(lines):
        return None
    start, end = lines[:, 0:2], lines[:, 2:4]
    vector = end - start
    length_sq = np.maximum((vector**2).sum(axis=1), 1e-9)
    t = np.clip(((np.asarray(center) - start) * vector).sum(axis=1) / length_sq, 0.0, 1.0)
    nearest = start + vector * t[:, None]
    distance = np.hypot(*(nearest - np.asarray(center)).T)
    index = int(np.argmin(distance))
    if distance[index] > radius_px:
        return None
    return float(distance[index]), float(np.sqrt(length_sq[index]))


def _plausible(px_per_m: float) -> bool:
    return 15.0 <= px_per_m <= 500.0


def _scale_bar(page: PlanPage) -> ScaleEstimate | None:
    lines = _line_array(page)
    best: tuple[float, float] | None = None
    for span in page.texts:
        numbers = [float(value) for value in _METER.findall(span.text)]
        meters = max(numbers) if numbers else 0.0
        if meters < 0.5:
            continue
        hit = _closest_line(lines, span.center, 80.0)
        if hit is None or not _plausible(hit[1] / meters):
            continue
        if best is None or hit[0] < best[0]:
            best = (hit[0], hit[1] / meters)
    if best is None:
        return None
    return ScaleEstimate(px_per_m=best[1], method="scale_bar", confidence=0.95)


def _dimension(page: PlanPage) -> ScaleEstimate | None:
    """Dimension strings beside lines. Several must agree within 5%.

    Room numbers on CAD sheets ("3401") parse as millimetres and sit beside
    random walls, so a lone candidate is trusted only when it is the only one.
    """

    lines = _line_array(page)
    candidates = []
    for span in page.texts:
        meters = _parse_dimension_meters(span.text)
        if meters is None or meters < 0.5:
            continue
        hit = _closest_line(lines, span.center, 70.0)
        if hit is not None and _plausible(hit[1] / meters):
            candidates.append(hit[1] / meters)
    if not candidates:
        return None
    support = [[other for other in candidates if abs(other - value) <= 0.05 * value] for value in candidates]
    best = max(support, key=len)
    if len(best) < 2 and len(candidates) > 1:
        return None
    return ScaleEstimate(px_per_m=float(median(best)), method="dimension_text", confidence=0.8)


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
