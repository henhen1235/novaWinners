"""Render Swiss Dwellings floors (CC BY 4.0, zenodo.org/records/7070952) to raster plans with truth.

Input is a head slice of ``geometries.csv`` (WKT in metres, y up). Each floor
becomes ``<out>/<floor_id>/plan.png`` plus ``truth.scene.json`` in the same
shape the scorer reads. Walls are filled black, doors are white gaps with a
thin swing arc, windows are white gaps with a thin outline (as drawn on real
plans), room names are printed at the room centroid, and a 0-5 m scale bar
sits above the plan. Image y is down: ``z = maxy - y``.
``render_plan`` is shared with ``make_curved_plan.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from shapely import wkt
from shapely import affinity
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

WALKABLE_TYPES = {
    "ROOM", "BATHROOM", "CORRIDOR", "KITCHEN", "BEDROOM", "LIVING_DINING", "STOREROOM",
    "LIVING_ROOM", "KITCHEN_DINING", "DINING", "STAIRCASE",
}
PAD_M = 2.0
WALK_INSET_M = 0.25


def read_floors(csv_path: Path) -> dict[str, list[dict]]:
    csv.field_size_limit(10**9)
    floors: dict[str, list[dict]] = defaultdict(list)
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                geometry = wkt.loads(row["geometry"])
            except Exception:  # noqa: BLE001 - the head slice ends mid-row
                continue
            floors[row["floor_id"]].append({"type": row["entity_type"], "subtype": row["entity_subtype"], "geom": geometry})
    return floors


def render_floor(items: list[dict], out_dir: Path, px_per_m: float) -> None:
    walls = [item["geom"] for item in items if item["subtype"] == "WALL"]
    areas = [item for item in items if item["type"] == "area" and item["subtype"] in WALKABLE_TYPES]
    minx, _miny, _maxx, maxy = unary_union(walls + [area["geom"] for area in areas]).bounds

    def flip(geom):
        return affinity.scale(affinity.translate(geom, -minx, -maxy), 1.0, -1.0, origin=(0, 0))

    doors = [(flip(item["geom"]), item["subtype"] == "ENTRANCE_DOOR") for item in items if item["subtype"] in {"DOOR", "ENTRANCE_DOOR"}]
    windows = [flip(item["geom"]) for item in items if item["subtype"] == "WINDOW"]
    rooms = [(flip(area["geom"]), area["subtype"].replace("_", " ").title()) for area in areas]
    render_plan([flip(wall) for wall in walls], rooms, doors, windows, out_dir, px_per_m)


def render_plan(walls, rooms, doors, windows, out_dir: Path, px_per_m: float) -> None:
    """Draw a plan already in image metres (y down). ``rooms`` is (polygon, label), ``doors`` is (polygon, is_exit)."""

    everything = unary_union(list(walls) + [room for room, _label in rooms])
    minx, miny, maxx, maxy = everything.bounds
    shift = (PAD_M - minx, PAD_M - miny)
    width = int(math.ceil((maxx - minx + 2 * PAD_M) * px_per_m))
    height = int(math.ceil((maxy - miny + 2 * PAD_M) * px_per_m))

    def to_m(x: float, y: float) -> tuple[float, float]:
        return (x + shift[0], y + shift[1])

    def to_px(poly) -> np.ndarray:
        return np.array([[to_m(x, y)[0] * px_per_m, to_m(x, y)[1] * px_per_m] for x, y in poly.exterior.coords], np.int32)

    image = np.full((height, width, 3), 255, np.uint8)
    for wall in _polys(walls):
        cv2.fillPoly(image, [to_px(wall)], (0, 0, 0))
        for hole in wall.interiors:
            cv2.fillPoly(image, [to_px(Polygon(hole))], (255, 255, 255))
    for window in _polys(windows):
        cv2.fillPoly(image, [to_px(window)], (255, 255, 255))
        cv2.polylines(image, [to_px(window)], True, (0, 0, 0), 1)
    door_segments = []
    for door, is_exit in doors:
        for poly in _polys([door]):
            cv2.fillPoly(image, [to_px(poly)], (255, 255, 255))
            p1, p2 = _long_axis(poly)
            door_segments.append(([*to_m(*p1)], [*to_m(*p2)], is_exit))
            _draw_swing(image, to_m(*p1), to_m(*p2), px_per_m)
    for room, label in rooms:
        center = room.representative_point()
        cx, cz = to_m(center.x, center.y)
        size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
        cv2.putText(image, label, (int(cx * px_per_m - size[0] / 2), int(cz * px_per_m)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    bar_y = int(0.6 * px_per_m)
    cv2.line(image, (int(0.5 * px_per_m), bar_y), (int(5.5 * px_per_m), bar_y), (0, 0, 0), 2)
    cv2.putText(image, "0      5 m", (int(0.5 * px_per_m), bar_y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / "plan.png"), image)
    moved = [affinity.translate(room, *shift) for room, _label in rooms]
    _write_truth(out_dir, moved, door_segments)


def _write_truth(out_dir: Path, rooms, door_segments) -> None:
    rooms = [poly for poly in _polys(rooms)]
    walk = unary_union(rooms).buffer(-WALK_INSET_M)
    strips = [LineString([a, b]).buffer(0.3) for a, b, _e in door_segments]
    walk = unary_union([walk] + strips).intersection(unary_union(rooms).buffer(0.3))
    polygons = [
        {"exterior": [list(c) for c in part.exterior.coords], "holes": [[list(c) for c in hole.coords] for hole in part.interiors]}
        for part in _polys([walk])
    ]
    truth = {
        "meta": {"scale_method": "truth"},
        "rooms": [{"id": f"r{index}"} for index in range(len(rooms))],
        "doors": [{"segment": [a, b]} for a, b, _e in door_segments],
        "exits": [{"position": [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]} for a, b, is_exit in door_segments if is_exit],
        "walkable": {"L1": {"polygons": polygons}},
    }
    (out_dir / "truth.scene.json").write_text(json.dumps(truth) + "\n", encoding="utf-8")


def _polys(geoms) -> list[Polygon]:
    out = []
    for geom in geoms:
        if geom.is_empty:
            continue
        out.extend(geom.geoms if hasattr(geom, "geoms") else [geom])
    return [poly for poly in out if poly.geom_type == "Polygon"]


def _long_axis(poly: Polygon) -> tuple[tuple[float, float], tuple[float, float]]:
    corners = list(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    a, b = max(edges, key=lambda e: math.dist(*e))
    c, d = edges[(edges.index((a, b)) + 2) % 4]
    return ((a[0] + d[0]) / 2, (a[1] + d[1]) / 2), ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)


def _draw_swing(image: np.ndarray, p1, p2, px_per_m: float) -> None:
    radius = math.dist(p1, p2) * px_per_m
    center = (int(p1[0] * px_per_m), int(p1[1] * px_per_m))
    start = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
    cv2.ellipse(image, center, (int(radius), int(radius)), 0, start, start + 90, (0, 0, 0), 1, cv2.LINE_AA)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--px-per-m", type=float, default=60.0)
    parser.add_argument("--min-entities", type=int, default=60)
    parser.add_argument("--max-entities", type=int, default=260)
    args = parser.parse_args()
    floors = read_floors(args.csv)
    chosen = sorted(fid for fid, items in floors.items() if args.min_entities <= len(items) <= args.max_entities)
    step = max(1, len(chosen) // args.count)
    for floor_id in chosen[::step][: args.count]:
        render_floor(floors[floor_id], args.out / f"swiss_{floor_id}", args.px_per_m)
        print(args.out / f"swiss_{floor_id}")


if __name__ == "__main__":
    main()
