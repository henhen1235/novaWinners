"""Dense CAD pages as a wall raster.

A CAD export (thousands of hairline strokes, double-line walls, door gaps,
furniture in lighter grey) does not close into faces on the segment path, and
pairing tens of thousands of strokes is slow. Drawing only the dark strokes
into an image hands the page to the watershed room extractor, which handles
any wall angle and curves. Text keeps its position, rescaled to the new image.

When the PDF carries CAD layer names (optional content), the US National CAD
Standard layers are used: A-AREA room boundaries when present (closed rooms,
exact shapes), else A-WALL*/A-GLAZ*/S-COLS* walls; door swings come from
A-DOOR* arcs. Without layers the dark strokes are used.
"""

from __future__ import annotations

import cv2
import numpy as np

from ingest_blueprint.types import PlanPage, TextSpan

CAD_DENSE_STROKES = 3000
TARGET_PX = 24_000_000
DARK_MAX = 0.45


AREA_LAYERS = ("A-AREA",)
WALL_LAYERS = ("A-WALL", "A-GLAZ", "S-COLS")
DOOR_LAYERS = ("A-DOOR",)


def is_dense_cad(page: PlanPage) -> bool:
    return len(page.strokes) >= CAD_DENSE_STROKES or any(_on(stroke.layer, WALL_LAYERS + AREA_LAYERS) for stroke in page.strokes)


def _on(layer: str, names: tuple[str, ...]) -> bool:
    # "ARCH|A-WALL-TPTN": an xref prefix before "|", then the NCS name. "A-AREA-IDEN" is text, not a boundary.
    name = layer.rsplit("|", 1)[-1].upper()
    return name.startswith(names) and not name.endswith("-IDEN")


def _wall_strokes(page: PlanPage) -> list:
    for names in (AREA_LAYERS, WALL_LAYERS):
        layered = [stroke for stroke in page.strokes if _on(stroke.layer, names)]
        if len(layered) >= 20:
            return layered
    return [stroke for stroke in page.strokes if stroke.color is None or max(stroke.color) < DARK_MAX]


def _door_arcs(page: PlanPage) -> list[tuple[tuple[float, float], tuple[float, float], float]]:
    """Quarter-circle swings on door layers as (hinge, arc midpoint, radius) in page pixels."""

    from ingest_blueprint.scale import _circumcenter, _quarter_radius_px

    arcs = []
    for stroke in page.strokes:
        if not stroke.is_curve or not _on(stroke.layer, DOOR_LAYERS):
            continue
        radius = _quarter_radius_px(stroke.points)
        if radius is None:
            continue
        hinge = _circumcenter(stroke.points[0], stroke.points[len(stroke.points) // 2], stroke.points[-1])
        arcs.append((hinge, stroke.points[len(stroke.points) // 2], radius))
    return arcs


def wall_raster_page(page: PlanPage) -> PlanPage:
    """Wall strokes drawn 1 px wide (cropped to their extent), plus text rescaled to match."""

    dark = _wall_strokes(page)
    points = np.concatenate([np.asarray(stroke.points, np.float64) for stroke in dark]) if dark else np.zeros((1, 2))
    low = points.min(axis=0) - 20.0
    high = points.max(axis=0) + 20.0
    span = high - low
    factor = float(np.sqrt(TARGET_PX / max(span[0] * span[1], 1.0)))
    width, height = int(span[0] * factor) + 1, int(span[1] * factor) + 1
    image = np.full((height, width, 3), 255, np.uint8)
    polylines = [np.round((np.asarray(stroke.points) - low) * factor).astype(np.int32) for stroke in dark]
    cv2.polylines(image, polylines, False, (0, 0, 0), 1, cv2.LINE_8)
    texts = [
        TextSpan(
            text=span_.text,
            bbox=tuple(float(v) for v in ((np.array(span_.bbox).reshape(2, 2) - low) * factor).ravel()),
            size=span_.size,
        )
        for span_ in page.texts
    ]
    arcs = [
        (tuple((np.asarray(hinge) - low) * factor), tuple((np.asarray(mid) - low) * factor), radius * factor)
        for hinge, mid, radius in _door_arcs(page)
    ]
    return PlanPage(
        source_path=page.source_path,
        page_index=page.page_index,
        width_px=width,
        height_px=height,
        raster=image,
        strokes=[],
        texts=texts,
        dpi=page.dpi * factor,
        door_arcs=arcs,
    )

