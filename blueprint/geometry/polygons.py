"""Shapely helpers. Rings are [x, z] meters with no repeated closing point."""

from __future__ import annotations

from shapely.geometry import Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid


def flatten_polygons(geom: BaseGeometry) -> list[Polygon]:
    """Return polygonal parts, dropping empty and non-polygon pieces."""

    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return [poly for poly in geom.geoms if not poly.is_empty]
    if geom.geom_type == "GeometryCollection":
        parts: list[Polygon] = []
        for child in geom.geoms:
            parts.extend(flatten_polygons(child))
        return parts
    return []


def clean_geometry(geom: BaseGeometry, min_area_m2: float = 0.05) -> BaseGeometry:
    """Make a geometry valid and drop slivers below ``min_area_m2``."""

    fixed = make_valid(geom).buffer(0)
    parts = [poly for poly in flatten_polygons(fixed) if poly.area >= min_area_m2]
    if not parts:
        return Polygon()
    return unary_union(parts)


def ring_coords(ring) -> list[list[float]]:
    coords = list(ring.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return [[float(x), float(z)] for x, z in coords]


def polygon_to_rings(poly: Polygon) -> dict:
    return {
        "exterior": ring_coords(poly.exterior),
        "holes": [ring_coords(hole) for hole in poly.interiors],
    }


def geometry_to_rings(geom: BaseGeometry, min_area_m2: float = 0.01) -> list[dict]:
    return [polygon_to_rings(poly) for poly in flatten_polygons(geom) if poly.area >= min_area_m2]


def round_tree(value, digits: int = 4):
    """Round every float in a JSON-like tree so dumps are deterministic."""

    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {key: round_tree(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [round_tree(item, digits) for item in value]
    if isinstance(value, tuple):
        return [round_tree(item, digits) for item in value]
    return value


def shape_mapping(geom: BaseGeometry) -> dict:
    return mapping(geom)
