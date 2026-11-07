"""Optional IFC ingest. Install ``requirements-ifc.txt`` to enable it.

Each IfcWall / IfcDoor / IfcSpace / IfcStair / IfcSlab is projected onto the
floor. IFC is Z-up, so plan X stays X and plan Y becomes our Z. One scene
level is produced per IfcBuildingStorey.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ingest_blueprint.dxf_loader import PX_PER_M
from ingest_blueprint.types import PlanPage, Stroke, TextSpan


def load_ifc(path: str | Path) -> list[PlanPage]:
    try:
        import ifcopenshell
        import ifcopenshell.geom
    except ImportError as exc:
        raise ImportError(
            "IFC support is optional. Install it with: pip install -r requirements-ifc.txt"
        ) from exc

    model = ifcopenshell.open(str(path))
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    storeys = list(model.by_type("IfcBuildingStorey")) or [None]
    pages: list[PlanPage] = []
    for index, storey in enumerate(storeys):
        elements = _elements(model, storey) if storey is not None else list(model.by_type("IfcProduct"))
        strokes: list[Stroke] = []
        texts: list[TextSpan] = []
        for element in elements:
            kind = element.is_a()
            if kind not in {"IfcWall", "IfcWallStandardCase", "IfcDoor", "IfcSpace", "IfcStair", "IfcSlab"}:
                continue
            segment = _footprint(settings, element)
            if segment is None:
                continue
            layer = "WALL" if "Wall" in kind or kind == "IfcSlab" else kind
            width = 20.0 if "Wall" in kind or kind == "IfcSlab" else 2.0
            strokes.append(Stroke([segment[0], segment[1]], width, (0.0, 0.0, 0.0), False))
            if kind == "IfcSpace":
                name = getattr(element, "Name", None) or "Room"
                mid = ((segment[0][0] + segment[1][0]) / 2.0, (segment[0][1] + segment[1][1]) / 2.0)
                texts.append(TextSpan(str(name), (mid[0] - 10, mid[1] - 8, mid[0] + 10, mid[1] + 8), 12.0))
        if not strokes:
            continue
        pages.append(_page(path, index, strokes, texts, storey))
    if not pages:
        raise ValueError(f"No projectable walls in {path}")
    return pages


def _elements(model, storey) -> list:
    found = []
    for relation in model.by_type("IfcRelContainedInSpatialStructure"):
        if relation.RelatingStructure == storey:
            found.extend(relation.RelatedElements)
    return found


def _footprint(settings, element) -> tuple[tuple[float, float], tuple[float, float]] | None:
    import ifcopenshell.geom

    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
    except Exception:
        return None
    vertices = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
    if len(vertices) < 2:
        return None
    # IFC Z is up. Plan XZ uses IFC X and Y.
    points = vertices[:, :2]
    span = points.max(axis=0) - points.min(axis=0)
    if span[0] >= span[1]:
        y_value = float(np.median(points[:, 1]))
        start = (float(points[:, 0].min()), y_value)
        end = (float(points[:, 0].max()), y_value)
    else:
        x_value = float(np.median(points[:, 0]))
        start = (x_value, float(points[:, 1].min()))
        end = (x_value, float(points[:, 1].max()))
    # Scale meters into the loader's pixel space. Y is flipped by the page builder.
    return ((start[0] * PX_PER_M, start[1] * PX_PER_M), (end[0] * PX_PER_M, end[1] * PX_PER_M))


def _page(path, index: int, strokes: list[Stroke], texts: list[TextSpan], storey) -> PlanPage:
    xs = [point[0] for stroke in strokes for point in stroke.points]
    ys = [point[1] for stroke in strokes for point in stroke.points]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    pad = 40.0

    def flip(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] - minx + pad, maxy - point[1] + pad)

    flipped = [
        Stroke([flip(point) for point in stroke.points], stroke.width_px, stroke.color, stroke.is_curve)
        for stroke in strokes
    ]
    flipped_texts = []
    for span in texts:
        x0, y0, x1, y1 = span.bbox
        a = flip((x0, y0))
        b = flip((x1, y1))
        flipped_texts.append(TextSpan(span.text, (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])), span.size))
    if storey is not None and getattr(storey, "Name", None):
        flipped_texts.append(TextSpan(str(storey.Name), (pad, pad, pad + 40, pad + 16), 14.0))
    width = int(maxx - minx + 2 * pad) + 1
    height = int(maxy - miny + 2 * pad) + 1
    raster = np.full((max(height, 1), max(width, 1), 3), 255, dtype=np.uint8)
    return PlanPage(str(path), index, raster.shape[1], raster.shape[0], raster, flipped, flipped_texts, 200.0)
