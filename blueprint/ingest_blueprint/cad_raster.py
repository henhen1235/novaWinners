"""Dense CAD pages as a wall raster.

A CAD export (thousands of hairline strokes, double-line walls, door gaps,
furniture in lighter grey) does not close into faces on the segment path, and
pairing tens of thousands of strokes is slow. Drawing only the dark strokes
into an image hands the page to the watershed room extractor, which handles
any wall angle and curves. Text keeps its position, rescaled to the new image.
"""

from __future__ import annotations

import cv2
import numpy as np

from ingest_blueprint.types import PlanPage, TextSpan

CAD_DENSE_STROKES = 3000
TARGET_PX = 24_000_000
DARK_MAX = 0.45


def is_dense_cad(page: PlanPage) -> bool:
    return len(page.strokes) >= CAD_DENSE_STROKES


def wall_raster_page(page: PlanPage) -> PlanPage:
    """Dark strokes drawn 1 px wide (cropped to their extent), plus text rescaled to match."""

    dark = [stroke for stroke in page.strokes if stroke.color is None or max(stroke.color) < DARK_MAX]
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
    return PlanPage(
        source_path=page.source_path,
        page_index=page.page_index,
        width_px=width,
        height_px=height,
        raster=image,
        strokes=[],
        texts=texts,
        dpi=page.dpi * factor,
    )

