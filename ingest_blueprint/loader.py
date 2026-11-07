"""Load a blueprint page.

PDF vector coordinates from PyMuPDF are already top-left, y down. They are
scaled into pixels at 200 DPI so they line up with the raster. World z later
uses those pixel y values directly (not negated).
"""

from __future__ import annotations

from pathlib import Path

import fitz
import numpy as np

from ingest_blueprint.types import PlanPage, Stroke, TextSpan

DPI = 200.0
ZOOM = DPI / 72.0


def load(path: str | Path) -> list[PlanPage]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(source)
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return [_load_image(source)]
    if suffix == ".dxf":
        from ingest_blueprint.dxf_loader import load_dxf

        return load_dxf(source)
    if suffix == ".ifc":
        from ingest_blueprint.ifc_loader import load_ifc

        return load_ifc(source)
    raise ValueError(f"Unsupported blueprint type: {suffix}")


def _load_pdf(path: Path) -> list[PlanPage]:
    document = fitz.open(path)
    pages: list[PlanPage] = []
    matrix = fitz.Matrix(ZOOM, ZOOM)
    try:
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            raster = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
            if pixmap.n == 4:
                raster = raster[:, :, :3]
            raster = raster.copy()
            pages.append(
                PlanPage(
                    source_path=str(path),
                    page_index=index,
                    width_px=pixmap.width,
                    height_px=pixmap.height,
                    raster=raster,
                    strokes=_strokes(page),
                    texts=_texts(page),
                    dpi=DPI,
                )
            )
    finally:
        document.close()
    return pages


def _load_image(path: Path) -> PlanPage:
    import cv2

    raster = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if raster is None:
        raise ValueError(f"Could not read image {path}")
    raster = cv2.cvtColor(raster, cv2.COLOR_BGR2RGB)
    height, width = raster.shape[:2]
    return PlanPage(
        source_path=str(path),
        page_index=0,
        width_px=width,
        height_px=height,
        raster=raster,
        strokes=[],
        texts=[],
        dpi=DPI,
    )


def _sample_cubic(p0, p1, p2, p3, steps: int = 12) -> list[tuple[float, float]]:
    points = []
    for step in range(steps + 1):
        t = step / steps
        u = 1.0 - t
        x = (u * u * u * p0.x) + (3 * u * u * t * p1.x) + (3 * u * t * t * p2.x) + (t * t * t * p3.x)
        y = (u * u * u * p0.y) + (3 * u * u * t * p1.y) + (3 * u * t * t * p2.y) + (t * t * t * p3.y)
        points.append((x * ZOOM, y * ZOOM))
    return points


def _px(point) -> tuple[float, float]:
    return (float(point.x) * ZOOM, float(point.y) * ZOOM)


def _color(value) -> tuple[float, float, float] | None:
    if not value:
        return None
    if len(value) >= 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    return None


def _strokes(page) -> list[Stroke]:
    strokes: list[Stroke] = []
    seen: set[tuple] = set()
    for drawing in page.get_drawings():
        width_px = float(drawing.get("width") or 0.0) * ZOOM
        color = _color(drawing.get("color"))
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l":
                a = _px(item[1])
                b = _px(item[2])
                key = tuple(sorted((round(a[0], 2), round(a[1], 2), round(b[0], 2), round(b[1], 2))))
                if key in seen or a == b:
                    continue
                seen.add(key)
                strokes.append(Stroke([a, b], width_px, color, False))
            elif kind == "c":
                points = _sample_cubic(item[1], item[2], item[3], item[4])
                strokes.append(Stroke(points, width_px, color, True))
            elif kind == "re":
                rect = item[1]
                corners = [
                    (rect.x0 * ZOOM, rect.y0 * ZOOM),
                    (rect.x1 * ZOOM, rect.y0 * ZOOM),
                    (rect.x1 * ZOOM, rect.y1 * ZOOM),
                    (rect.x0 * ZOOM, rect.y1 * ZOOM),
                ]
                for start, end in zip(corners, corners[1:] + corners[:1]):
                    strokes.append(Stroke([start, end], max(width_px, 1.0), color, False))
    return strokes


def _texts(page) -> list[TextSpan]:
    spans: list[TextSpan] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            size = max((float(span.get("size", 8)) for span in line.get("spans", [])), default=8.0)
            spans.append(
                TextSpan(
                    text=text,
                    bbox=(x0 * ZOOM, y0 * ZOOM, x1 * ZOOM, y1 * ZOOM),
                    size=size,
                )
            )
    return spans
