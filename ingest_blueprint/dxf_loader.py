"""DXF floor plans on the same pixel page the PDF loader produces.

Drawing units are treated as meters. They are scaled to 100 px/m, which is the
synthetic plan's 200 DPI rate, and y is flipped so image y grows downward.
A thin 5 m scale bar is added when the file has no meter label, so the vector
pipeline can recover px/m without trusting raw DXF units alone.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ingest_blueprint.types import PlanPage, Stroke, TextSpan

PX_PER_M = 100.0


def load_dxf(path: str | Path) -> list[PlanPage]:
    import ezdxf

    document = ezdxf.readfile(str(path))
    lines: list[tuple[tuple[float, float], tuple[float, float], str]] = []
    curves: list[list[tuple[float, float]]] = []
    labels: list[tuple[str, tuple[float, float]]] = []
    for entity in document.modelspace():
        _collect(entity, lines, curves, labels)
    if not lines and not curves:
        raise ValueError(f"No drawable entities in {path}")
    if not any("m" in text.lower() for text, _point in labels):
        xs = [point[0] for line in lines for point in line[:2]]
        ys = [point[1] for line in lines for point in line[:2]]
        origin_y = min(ys) - 1.5
        origin_x = min(xs)
        lines.append(((origin_x, origin_y), (origin_x + 5.0, origin_y), "SCALE"))
        labels.append(("0  5 m", (origin_x + 2.5, origin_y + 0.4)))
    xs = [point[0] for line in lines for point in line[:2]]
    ys = [point[1] for line in lines for point in line[:2]]
    xs += [point[0] for curve in curves for point in curve]
    ys += [point[1] for curve in curves for point in curve]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    pad = 0.4

    def to_px(point: tuple[float, float]) -> tuple[float, float]:
        return ((point[0] - minx + pad) * PX_PER_M, (maxy - point[1] + pad) * PX_PER_M)

    strokes: list[Stroke] = []
    for start, end, layer in lines:
        width = 20.0 if _is_wall(layer) else 1.5
        strokes.append(Stroke([to_px(start), to_px(end)], width, (0.0, 0.0, 0.0), False))
    for curve in curves:
        strokes.append(Stroke([to_px(point) for point in curve], 1.5, (0.0, 0.0, 0.0), True))
    texts = []
    for text, anchor in labels:
        x_px, y_px = to_px(anchor)
        texts.append(TextSpan(text, (x_px - 20, y_px - 8, x_px + 20, y_px + 8), 12.0))
    width_px = int(math.ceil((maxx - minx + 2 * pad) * PX_PER_M))
    height_px = int(math.ceil((maxy - miny + 2 * pad) * PX_PER_M))
    raster = np.full((max(height_px, 1), max(width_px, 1), 3), 255, dtype=np.uint8)
    return [
        PlanPage(
            source_path=str(path),
            page_index=0,
            width_px=raster.shape[1],
            height_px=raster.shape[0],
            raster=raster,
            strokes=strokes,
            texts=texts,
            dpi=200.0,
        )
    ]


def _collect(entity, lines, curves, labels) -> None:
    kind = entity.dxftype()
    layer = str(getattr(entity.dxf, "layer", "0"))
    if kind == "LINE":
        start = (float(entity.dxf.start.x), float(entity.dxf.start.y))
        end = (float(entity.dxf.end.x), float(entity.dxf.end.y))
        lines.append((start, end, layer))
    elif kind == "LWPOLYLINE":
        points = [(float(point[0]), float(point[1])) for point in entity.get_points("xy")]
        for start, end in zip(points, points[1:]):
            lines.append((start, end, layer))
        if entity.closed and len(points) > 2:
            lines.append((points[-1], points[0], layer))
    elif kind == "ARC":
        curves.append(_sample_arc(entity))
    elif kind in {"TEXT", "MTEXT"}:
        text = entity.dxf.text if kind == "TEXT" else entity.plain_text()
        insert = entity.dxf.insert
        labels.append((str(text), (float(insert.x), float(insert.y))))
    elif kind == "INSERT":
        try:
            children = list(entity.virtual_entities())
        except Exception:
            children = []
        for child in children:
            _collect(child, lines, curves, labels)


def _sample_arc(entity) -> list[tuple[float, float]]:
    center = entity.dxf.center
    radius = float(entity.dxf.radius)
    start = math.radians(float(entity.dxf.start_angle))
    end = math.radians(float(entity.dxf.end_angle))
    if end <= start:
        end += 2.0 * math.pi
    points = []
    for step in range(13):
        angle = start + (end - start) * step / 12.0
        points.append((float(center.x) + radius * math.cos(angle), float(center.y) + radius * math.sin(angle)))
    return points


def _is_wall(layer: str) -> bool:
    name = layer.upper()
    return "WALL" in name or name in {"0", "A-WALL"}
