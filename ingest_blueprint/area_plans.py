"""Rooms from area tags printed on a CAD sheet, such as ``A115`` / ``806sf``.

The line tracer treats furniture and dimensions as walls. These sheets already
name each room and give its area, and the white space inside the walls is the
room. Scale comes from that printed area, not from a guessed door width.

Walls, glazing, and railings are taken from the CAD layer names (``A-WALL``,
``A-GLAZ``, ``A-FLOR-HRAL``). Stroke width on these sheets is a hairline, so
classification is by layer, not by line weight. Furniture, doors swings, and
the title block stay out of the wall list.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

import cv2
import fitz
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

from export.assemble import assemble_scene
from geometry.doors import cut_walls_for_doors
from geometry.rooms import room_type_from_name
from ingest_blueprint.pipeline import generated_by, publish_scene
from ingest_blueprint.types import DoorOpening, Segment

ZOOM = 2.0
WALL_M = 0.2
SF_TO_M2 = 0.092903
STAIR = re.compile(r"stair|stwy|staircase|\bST\b|\bDN\b", re.IGNORECASE)
AREA = re.compile(r"^([\d,]+)\s*sf$", re.IGNORECASE)
FLOOR_NAMES = ["Level A", "Level 1", "Level 2", "Level 3", "Level 4", "Level 5", "Level 6"]


def read_area_plans(source: str | Path, out_dir: str | Path, label: str = "") -> dict | None:
    """Build a scene when the PDF pages carry room-area tags. ``None`` otherwise."""

    path = Path(source)
    if path.suffix.lower() != ".pdf":
        return None
    document = fitz.open(path)
    try:
        name = _building_name(Path(label) if label else path)
        if name in {"plan", "Building"} and "TEPPER" in document[0].get_text("text")[:8000].upper():
            name = "CMU Tepper"
        levels = []
        for index, page in enumerate(document):
            level = _page_level(page, index)
            if level is not None:
                levels.append(level)
    finally:
        document.close()
    if sum(len(level["faces"]) for level in levels) < 8:
        return None
    scene, _walkable = assemble_scene(
        levels,
        meta_name=name,
        source_file=str(path),
        scale_method="dimension_text",
        scale_confidence=0.75,
        generated_by=generated_by(),
        source="blueprint",
    )
    counts = {"wall_runs": 0, "glazing_runs": 0, "railing_runs": 0}
    for level in levels:
        for key, value in level.get("barrier_counts", {}).items():
            counts[key] = counts.get(key, 0) + int(value)
    scene["extensions"]["barrier_counts"] = counts
    scene, code = publish_scene(scene, out_dir)
    return {
        "scene": scene,
        "exit_code": code,
        "building": name,
        "floors": len(levels),
        "reader": "area",
        "pages": list(range(len(levels))),
    }


def _building_name(path: Path) -> str:
    stem = path.stem.replace("-", " ").replace("_", " ")
    if "tepper" in stem.lower() or stem.lower().startswith("tsb"):
        return "CMU Tepper"
    return stem or "Building"


def _page_level(page, index: int) -> dict | None:
    zoom = ZOOM
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    if image.shape[2] == 4:
        image = image[:, :, :3]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    ink = (gray < 210).astype(np.uint8) * 255
    # 13 px seals a door swing without swallowing a whole room at this zoom.
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (13, 13)))
    interior = (closed == 0).astype(np.uint8) * 255
    height, width = interior.shape
    flood = interior.copy()
    mask = np.zeros((height + 2, width + 2), np.uint8)
    for seed in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        if flood[seed[1], seed[0]]:
            cv2.floodFill(flood, mask, seed, 0)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats((flood > 0).astype(np.uint8), 8)
    tags = _area_tags(page, zoom)
    if len(tags) < 4:
        return None
    samples = []
    for name, square_feet, x_px, y_px in tags:
        component = _component_at(labels, stats, x_px, y_px)
        if component is None:
            continue
        ratio = stats[component, cv2.CC_STAT_AREA] / (square_feet * SF_TO_M2)
        if ratio <= 0:
            continue
        samples.append((name, square_feet, component, ratio, x_px, y_px))
    center = _scale_cluster([sample[3] for sample in samples])
    if center is None:
        return None
    ppm = center ** 0.5
    if not 3.0 <= ppm <= 80.0:
        return None
    chosen: dict[int, tuple] = {}
    for name, square_feet, component, ratio, x_px, y_px in samples:
        area_m2 = stats[component, cv2.CC_STAT_AREA] / center
        if not 2.0 <= area_m2 <= 500.0:
            continue
        current = chosen.get(component)
        if current is None or abs(ratio - center) < abs(current[2] - center):
            chosen[component] = (name, square_feet, ratio, x_px, y_px)
    faces = []
    labels_out = []
    stair_indexes = set()
    for component, (name, _square_feet, _ratio, _x, _y) in chosen.items():
        polygon = _polygon(labels, component, ppm)
        if polygon is None or polygon.area < 2.0 or polygon.area > 800:
            continue
        room_type = "stairwell" if STAIR.search(name) else room_type_from_name(name)
        if room_type not in {
            "office",
            "corridor",
            "stairwell",
            "lobby",
            "restroom",
            "classroom",
            "storage",
            "mechanical",
            "unknown",
        }:
            room_type = "unknown"
        if room_type == "stairwell":
            stair_indexes.add(len(faces))
        faces.append(polygon)
        labels_out.append((name[:80], room_type))
    if len(faces) < 3:
        return None
    doors = _bridge_doors(faces)
    drawn, barrier_counts = _sheet_barriers(page, zoom, ppm, faces)
    walk_base = None
    openings: list[Polygon] = []
    if len(drawn) >= 8:
        walls = _punch_openings(drawn, doors)
        if barrier_counts.get("railing_runs", 0):
            room_faces = [
                face
                for face, (name, _kind) in zip(faces, labels_out)
                if not str(name).upper().startswith("OTB")
            ]
            walk_base, openings = _railing_openings(page, ppm, drawn, tags, room_faces)
    else:
        walls = cut_walls_for_doors(_room_edge_walls(faces), doors)
    return {
        "level_id": f"L{index + 1}",
        "name": FLOOR_NAMES[index] if index < len(FLOOR_NAMES) else f"Level {index + 1}",
        "elevation_m": index * 3.0,
        "ceiling_height_m": 2.7,
        "walls": walls,
        "doors": doors,
        "faces": faces,
        "labels": labels_out,
        "obstacles": [],
        "walk_base": walk_base,
        "openings": openings,
        "stair_indexes": stair_indexes,
        "px_per_m": ppm,
        "barrier_counts": barrier_counts,
    }


def _area_tags(page, zoom: float) -> list[tuple[str, float, float, float]]:
    items = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if text:
                items.append((text, line["bbox"]))
    tags = []
    for index, (text, box) in enumerate(items):
        match = AREA.fullmatch(text)
        if not match:
            continue
        square_feet = float(match.group(1).replace(",", ""))
        if square_feet < 8 or square_feet > 20000:
            continue
        center_x = (box[0] + box[2]) / 2.0
        center_y = (box[1] + box[3]) / 2.0
        name = "Room"
        if index:
            previous, previous_box = items[index - 1]
            previous_x = (previous_box[0] + previous_box[2]) / 2.0
            previous_y = (previous_box[1] + previous_box[3]) / 2.0
            if abs(previous_x - center_x) < 40 and 0 < center_y - previous_y < 24 and not AREA.fullmatch(previous):
                name = previous
        tags.append((name, square_feet, center_x * zoom, center_y * zoom))
    return tags


def _component_at(labels: np.ndarray, stats: np.ndarray, x_px: float, y_px: float) -> int | None:
    height, width = labels.shape
    best = None
    best_area = 10**12
    for dy in range(-30, 34, 6):
        for dx in range(-30, 34, 6):
            x = int(x_px) + dx
            y = int(y_px) + dy
            if not (0 <= x < width and 0 <= y < height):
                continue
            component = int(labels[y, x])
            if component == 0:
                continue
            area = int(stats[component, cv2.CC_STAT_AREA])
            if not 400 < area < 250000:
                continue
            if area < best_area:
                best = component
                best_area = area
    return best


def _scale_cluster(ratios: list[float]) -> float | None:
    if len(ratios) < 4:
        return None
    logs = np.sort(np.log(np.array(ratios, dtype=float)))
    best: np.ndarray | None = None
    for start in logs:
        group = logs[(logs >= start) & (logs <= start + 0.55)]
        if best is None or len(group) > len(best):
            best = group
    if best is None or len(best) < 4:
        return None
    return float(np.median(np.exp(best)))


def _polygon(labels: np.ndarray, component: int, ppm: float) -> Polygon | None:
    mask = (labels == component).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(contour, 3.0, True)
    points = [(float(point[0][0]) / ppm, float(point[0][1]) / ppm) for point in approx]
    if len(points) < 3:
        return None
    polygon = Polygon(points).buffer(0)
    if polygon.is_empty or polygon.geom_type != "Polygon":
        return None
    return polygon


# CAD layer suffix after the discipline prefix. Exact match so A-WALL does not
# swallow A-WALL-TPTN hatch.
_WALL_LAYERS = {"A-WALL"}
_GLAZE_LAYERS = {"A-GLAZ", "A-GLAZ-CURT"}
_RAIL_LAYERS = {"A-FLOR-HRAL"}
# Hairline CAD width is a symbol. These are display thicknesses in meters.
# Thickness is a display width. The PDF stroke width is 0 (hairline), and a
# 0.2 m line disappears at a full-floor camera, so walls are drawn heavy
# enough to read as the building outline. Railings stay thinner and shorter.
_BARRIER_STYLE = {
    "wall": (0.45, None, 0.35),
    "glazing": (0.3, None, 0.7),
    "railing": (0.22, 1.1, 0.55),
}


def _sheet_barriers(page, zoom: float, ppm: float, faces: list[Polygon]) -> tuple[list[Segment], dict[str, int]]:
    """Wall, glazing, and railing centerlines in the same meter space as the rooms."""

    minx, miny, maxx, maxy = _cluster_box(faces, margin_m=12.0)
    grouped: dict[str, list[Segment]] = {"wall": [], "glazing": [], "railing": []}
    for drawing in page.get_drawings():
        kind = _barrier_kind(drawing.get("layer") or "")
        if kind is None:
            continue
        thickness, height, min_length = _BARRIER_STYLE[kind]
        for start, end in _drawing_lines(drawing):
            p1 = (start[0] * zoom / ppm, start[1] * zoom / ppm)
            p2 = (end[0] * zoom / ppm, end[1] * zoom / ppm)
            if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 0.12:
                continue
            mid_x = (p1[0] + p2[0]) / 2.0
            mid_y = (p1[1] + p2[1]) / 2.0
            if not (minx <= mid_x <= maxx and miny <= mid_y <= maxy):
                continue
            grouped[kind].append(Segment(p1, p2, thickness, height))
    counts = {}
    kept: list[Segment] = []
    for kind, segments in grouped.items():
        merged = _merge_runs(segments)
        min_length = _BARRIER_STYLE[kind][2]
        runs = [segment for segment in merged if segment.length_m >= min_length]
        counts[f"{kind}_runs"] = len(runs)
        kept.extend(runs)
    return kept, counts


def _barrier_kind(layer: str) -> str | None:
    name = layer.split("|")[-1].strip().upper()
    if name in _WALL_LAYERS:
        return "wall"
    if name in _GLAZE_LAYERS:
        return "glazing"
    if name in _RAIL_LAYERS:
        return "railing"
    return None


def _drawing_lines(drawing) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    lines = []
    for item in drawing.get("items") or []:
        kind = item[0]
        if kind == "l":
            lines.append((_xy(item[1]), _xy(item[2])))
        elif kind == "c" and len(item) >= 5:
            points = _cubic(_xy(item[1]), _xy(item[2]), _xy(item[3]), _xy(item[4]))
            lines.extend(zip(points, points[1:]))
        elif kind == "qu":
            corners = _quad_corners(item[1])
            if len(corners) >= 4:
                loop = corners[:4] + corners[:1]
                lines.extend(zip(loop, loop[1:]))
        elif kind == "re":
            rect = item[1]
            corners = [(rect.x0, rect.y0), (rect.x1, rect.y0), (rect.x1, rect.y1), (rect.x0, rect.y1)]
            loop = corners + corners[:1]
            lines.extend(((a, b) for a, b in zip(loop, loop[1:])))
    return lines


def _xy(point) -> tuple[float, float]:
    if hasattr(point, "x"):
        return (float(point.x), float(point.y))
    return (float(point[0]), float(point[1]))


def _quad_corners(quad) -> list[tuple[float, float]]:
    if hasattr(quad, "ul"):
        return [_xy(quad.ul), _xy(quad.ur), _xy(quad.lr), _xy(quad.ll)]
    return [_xy(point) for point in quad]


def _cubic(p0, p1, p2, p3, steps: int = 4) -> list[tuple[float, float]]:
    points = []
    for index in range(steps + 1):
        t = index / steps
        u = 1.0 - t
        points.append(
            (
                u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
            )
        )
    return points


def _cluster_box(faces: list[Polygon], margin_m: float) -> tuple[float, float, float, float]:
    minx = min(face.bounds[0] for face in faces) - margin_m
    miny = min(face.bounds[1] for face in faces) - margin_m
    maxx = max(face.bounds[2] for face in faces) + margin_m
    maxy = max(face.bounds[3] for face in faces) + margin_m
    return minx, miny, maxx, maxy


def _merge_runs(segments: list[Segment]) -> list[Segment]:
    """Join collinear pieces of the same stroke, including small gaps between them."""

    bins: dict[tuple, list[Segment]] = defaultdict(list)
    for segment in segments:
        dx = segment.p2[0] - segment.p1[0]
        dy = segment.p2[1] - segment.p1[1]
        if dx * dx + dy * dy < 1e-8:
            continue
        angle = math.atan2(dy, dx) % math.pi
        direction = (math.cos(angle), math.sin(angle))
        normal = (-direction[1], direction[0])
        offset = segment.p1[0] * normal[0] + segment.p1[1] * normal[1]
        key = (
            round(angle / 0.04),
            round(offset / 0.08),
            round(segment.thickness_m, 3),
            None if segment.height_m is None else round(segment.height_m, 2),
        )
        bins[key].append(segment)
    merged: list[Segment] = []
    for group in bins.values():
        sample = group[0]
        dx = sample.p2[0] - sample.p1[0]
        dy = sample.p2[1] - sample.p1[1]
        angle = math.atan2(dy, dx) % math.pi
        direction = (math.cos(angle), math.sin(angle))
        normal = (-direction[1], direction[0])
        offsets = []
        intervals = []
        for segment in group:
            offsets.append(segment.p1[0] * normal[0] + segment.p1[1] * normal[1])
            a = segment.p1[0] * direction[0] + segment.p1[1] * direction[1]
            b = segment.p2[0] * direction[0] + segment.p2[1] * direction[1]
            intervals.append((min(a, b), max(a, b)))
        offset = sorted(offsets)[len(offsets) // 2]
        intervals.sort()
        start, end = intervals[0]
        runs = []
        for left, right in intervals[1:]:
            if left <= end + 0.35:
                end = max(end, right)
            else:
                runs.append((start, end))
                start, end = left, right
        runs.append((start, end))
        for left, right in runs:
            if right - left < 0.05:
                continue
            p1 = (direction[0] * left + normal[0] * offset, direction[1] * left + normal[1] * offset)
            p2 = (direction[0] * right + normal[0] * offset, direction[1] * right + normal[1] * offset)
            merged.append(Segment(p1, p2, sample.thickness_m, sample.height_m))
    return merged


_OTB_NAME = re.compile(r"^OTB\d*", re.IGNORECASE)
_OPENING_RES_M = 0.30


def _floor_plate(segments: list[Segment], close_m: float = 1.25):
    """Interior of the exterior wall loop.

    Door-width gaps close. A courtyard left open by the wall outline stays
    outside the plate. Holes fully surrounded by walls are filled here and
    cut later when a railing (or an open-to-below tag) marks them as voids.
    """

    strokes = []
    for segment in segments:
        if segment.height_m is not None or segment.length_m < 0.4:
            continue
        strokes.append(LineString([segment.p1, segment.p2]).buffer(close_m, cap_style=1))
    if not strokes:
        return Polygon()
    thick = unary_union(strokes)
    parts = list(thick.geoms) if thick.geom_type == "MultiPolygon" else [thick]
    plates = []
    for part in parts:
        if part.geom_type != "Polygon" or part.area < 70:
            continue
        filled = Polygon(part.exterior)
        shrunk = filled.buffer(-close_m * 0.85)
        if shrunk.is_empty or shrunk.area < 50 or shrunk.area < part.area * 0.35:
            continue
        plates.append(shrunk)
    if not plates:
        return Polygon()
    return unary_union(plates)


def _otb_seeds(page, ppm: float) -> list[tuple[float, float]]:
    """Open-to-below labels, in the same meter space as the walls."""

    seeds = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not _OTB_NAME.match(text):
                continue
            box = line["bbox"]
            seeds.append(
                (
                    ((box[0] + box[2]) / 2.0) * ZOOM / ppm,
                    ((box[1] + box[3]) / 2.0) * ZOOM / ppm,
                )
            )
    return seeds


def _room_anchor_points(tags, ppm: float) -> list[tuple[float, float]]:
    points = []
    for name, _square_feet, x_px, y_px in tags:
        if _OTB_NAME.match(name):
            continue
        points.append((x_px / ppm, y_px / ppm))
    return points


def _railing_openings(page, ppm: float, barriers: list[Segment], tags, faces: list[Polygon]) -> tuple:
    """Floor plate, and the open regions on the far side of the railings.

    An open-to-below tag sits in the void. Railings and walls stop the flood
    so the corridor on the building side stays. A tagless region is removed
    only when it is wide, touches a railing, and sits far from every room tag.
    """

    plate = _floor_plate(barriers)
    if plate.is_empty:
        return None, []
    structural = [segment for segment in barriers if segment.height_m is None and segment.length_m >= 0.35]
    railings = [segment for segment in barriers if segment.height_m is not None and segment.length_m >= 0.5]
    seeds = _otb_seeds(page, ppm)
    anchors = _room_anchor_points(tags, ppm)
    if not railings and not seeds:
        return plate, []
    voids = _void_polygons(plate, structural, railings, seeds, anchors)
    # Floor that is not a named room and not a corridor beside one. A long
    # atrium or a skylight fails the compactness test when it is a thin
    # corridor network, and a whole open level stays put because it is huge.
    voids.extend(_compact_gaps(plate, faces))
    return plate, voids


def _compact_gaps(plate, faces: list[Polygon]) -> list[Polygon]:
    usable = [face for face in faces if face is not None and not face.is_empty and face.area > 1]
    if not usable or plate.is_empty:
        return []
    extra = plate.difference(unary_union(usable).buffer(4.0))
    if extra.is_empty:
        return []
    parts = list(extra.geoms) if extra.geom_type == "MultiPolygon" else [extra]
    gaps = []
    for part in parts:
        if part.geom_type != "Polygon" or not 400 <= part.area <= 1800:
            continue
        perimeter = part.exterior.length
        compact = 4 * math.pi * part.area / max(perimeter * perimeter, 1.0)
        if compact < 0.30:
            continue
        gaps.append(part)
    return gaps


def _void_polygons(plate, structural, railings, seeds, anchors) -> list[Polygon]:
    res = _OPENING_RES_M
    minx, miny, maxx, maxy = plate.bounds
    pad = 3.0
    origin = (minx - pad, miny - pad)
    width = int((maxx - minx + 2 * pad) / res) + 3
    height = int((maxy - miny + 2 * pad) / res) + 3
    if width > 4000 or height > 4000:
        return []

    def to_px(x: float, y: float) -> tuple[int, int]:
        return int(round((x - origin[0]) / res)), int(round((y - origin[1]) / res))

    plate_mask = np.zeros((height, width), np.uint8)
    _fill_geom(plate_mask, plate, to_px)
    wall_mask = np.zeros((height, width), np.uint8)
    _stroke_segments(wall_mask, structural, to_px)
    wall_radius = max(1, int(round(0.95 / res)))
    wall_mask = cv2.dilate(wall_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * wall_radius + 1, 2 * wall_radius + 1)))
    rail_mask = np.zeros((height, width), np.uint8)
    _stroke_segments(rail_mask, railings, to_px)
    rail_radius = max(1, int(round(1.35 / res)))
    rail_mask = cv2.dilate(rail_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rail_radius + 1, 2 * rail_radius + 1)))
    free = ((plate_mask > 0) & (wall_mask == 0) & (rail_mask == 0)).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(free, 4)
    rail_touch = cv2.dilate(rail_mask, np.ones((3, 3), np.uint8))
    touching = set(int(value) for value in np.unique(labels[rail_touch > 0]) if value)

    def anchors_in(mask: np.ndarray) -> int:
        found = 0
        for x, y in anchors:
            column, row = to_px(x, y)
            if 0 <= row < height and 0 <= column < width and mask[row, column]:
                found += 1
        return found

    def nearest_anchor(x: float, y: float) -> float:
        best = 1e9
        for ax, ay in anchors:
            distance = math.hypot(ax - x, ay - y)
            if distance < best:
                best = distance
        return best

    def label_at(x: float, y: float) -> int:
        column, row = to_px(x, y)
        if not (0 <= row < height and 0 <= column < width):
            return 0
        label = int(labels[row, column])
        if label:
            return label
        for radius in range(1, int(3.5 / res) + 1):
            row0, row1 = max(0, row - radius), min(height, row + radius + 1)
            column0, column1 = max(0, column - radius), min(width, column + radius + 1)
            patch = labels[row0:row1, column0:column1]
            present = patch[patch > 0]
            if len(present):
                return int(np.bincount(present).argmax())
        return 0

    void_mask = np.zeros((height, width), np.uint8)
    used: set[int] = set()

    def keep_component(label: int, require_far: bool) -> None:
        if label <= 0 or label in used:
            return
        area = float(stats[label, cv2.CC_STAT_AREA]) * res * res
        if area < 12 or area > plate.area * 0.45:
            return
        mask = labels == label
        real = anchors_in(mask)
        if real >= 1:
            # One nearby room tag must not keep an atrium. Keep only its wide empty core.
            salvaged = _salvage_wide_core(mask, to_px, seeds, res)
            if salvaged is None:
                return
            core_area = float(salvaged.sum()) * res * res
            kept_anchors = anchors_in(salvaged)
            if core_area < 40 or kept_anchors >= 4 or (kept_anchors >= 1 and kept_anchors * 220 > core_area):
                return
            void_mask[salvaged] = 255
            used.add(label)
            return
        if require_far:
            # A tagless corridor sits next to a room. An atrium is wide and alone.
            if label not in touching or area < 40 or area > 2500 or area > plate.area * 0.22:
                return
            center = centroids[label]
            meters = (origin[0] + float(center[0]) * res, origin[1] + float(center[1]) * res)
            if nearest_anchor(*meters) < 9.0:
                return
            if not _wide_core(mask, res, 2.6, 22):
                return
        elif area >= 90 and not _wide_core(mask, res, 1.8, 12):
            return
        void_mask[mask] = 255
        used.add(label)

    for x, y in seeds:
        keep_component(label_at(x, y), require_far=False)
    for label in range(1, count):
        keep_component(label, require_far=True)

    return _polygons_from_mask(void_mask, origin, res, plate)


def _wide_core(mask: np.ndarray, res: float, radius_m: float, min_area: float) -> bool:
    radius = max(1, int(round(radius_m / res)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    core = cv2.erode(mask.astype(np.uint8) * 255, kernel)
    return float(core.sum() / 255) * res * res >= min_area


def _salvage_wide_core(mask: np.ndarray, to_px, seeds, res: float) -> np.ndarray | None:
    """Keep the wide part of a void that leaked into rooms through one gap.

    The open-to-below tag often sits near the railing, so the eroded core of
    the atrium is a few meters away. Take the largest wide piece near a tag.
    """

    radius = max(1, int(round(2.4 / res)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    image = mask.astype(np.uint8) * 255
    core = cv2.erode(image, kernel)
    if not core.any():
        return None
    count, pieces = cv2.connectedComponents((core > 0).astype(np.uint8), 4)
    window = int(12.0 / res)
    best_label = 0
    best_area = 0
    for x, y in seeds:
        column, row = to_px(x, y)
        if not (0 <= row < pieces.shape[0] and 0 <= column < pieces.shape[1]):
            continue
        if not image[row, column] and not _near_mask(image, row, column, window // 3):
            continue
        row0, row1 = max(0, row - window), min(pieces.shape[0], row + window + 1)
        column0, column1 = max(0, column - window), min(pieces.shape[1], column + window + 1)
        patch = pieces[row0:row1, column0:column1]
        for label in np.unique(patch):
            if not label:
                continue
            area = int((pieces == label).sum())
            if area > best_area:
                best_area = area
                best_label = int(label)
    if not best_label or best_label >= count:
        return None
    restored = cv2.dilate((pieces == best_label).astype(np.uint8) * 255, kernel)
    restored = (restored > 0) & (image > 0)
    if float(restored.sum()) * res * res < 40:
        return None
    return restored


def _near_mask(image: np.ndarray, row: int, column: int, radius: int) -> bool:
    row0, row1 = max(0, row - radius), min(image.shape[0], row + radius + 1)
    column0, column1 = max(0, column - radius), min(image.shape[1], column + radius + 1)
    return bool(image[row0:row1, column0:column1].any())


def _fill_geom(mask: np.ndarray, geom, to_px) -> None:
    if geom.is_empty:
        return
    if geom.geom_type == "MultiPolygon":
        for part in geom.geoms:
            _fill_geom(mask, part, to_px)
        return
    if geom.geom_type != "Polygon":
        return
    cv2.fillPoly(mask, [np.array([to_px(x, y) for x, y in geom.exterior.coords], np.int32)], 255)
    for hole in geom.interiors:
        cv2.fillPoly(mask, [np.array([to_px(x, y) for x, y in hole.coords], np.int32)], 0)


def _stroke_segments(mask: np.ndarray, segments, to_px) -> None:
    for segment in segments:
        cv2.line(mask, to_px(*segment.p1), to_px(*segment.p2), 255, 1)


def _polygons_from_mask(mask: np.ndarray, origin: tuple[float, float], res: float, plate) -> list[Polygon]:
    contours, _ = cv2.findContours((mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygons = []
    for contour in contours:
        approx = cv2.approxPolyDP(contour, max(1.0, 0.7 / res), True)
        if len(approx) < 3:
            continue
        points = [(origin[0] + float(point[0][0]) * res, origin[1] + float(point[0][1]) * res) for point in approx]
        polygon = Polygon(points).buffer(0)
        if polygon.is_empty:
            continue
        polygon = polygon.intersection(plate)
        if polygon.is_empty:
            continue
        parts = list(polygon.geoms) if polygon.geom_type == "MultiPolygon" else [polygon]
        for part in parts:
            if part.geom_type == "Polygon" and part.area >= 12:
                polygons.append(part)
    return polygons


def _punch_openings(walls: list[Segment], doors: list[DoorOpening]) -> list[Segment]:
    """Cut a doorway through structural strokes near each room-to-room bridge.

    Railings stay continuous so an atrium or balcony edge is not nibbled away.
    """

    structural = [wall for wall in walls if wall.height_m is None]
    railings = [wall for wall in walls if wall.height_m is not None]
    for door in doors:
        mid = door.midpoint
        opening = min(max(door.width_m, 0.9), 1.3)
        nxt: list[Segment] = []
        for wall in structural:
            if _point_segment_distance(mid, wall) > 0.5:
                nxt.append(wall)
                continue
            nxt.extend(_cut_gap(wall, mid, opening))
        structural = nxt
    return structural + railings


def _point_segment_distance(point: tuple[float, float], wall: Segment) -> float:
    dx = wall.p2[0] - wall.p1[0]
    dy = wall.p2[1] - wall.p1[1]
    length2 = dx * dx + dy * dy
    if length2 < 1e-8:
        return math.hypot(point[0] - wall.p1[0], point[1] - wall.p1[1])
    t = ((point[0] - wall.p1[0]) * dx + (point[1] - wall.p1[1]) * dy) / length2
    t = max(0.0, min(1.0, t))
    return math.hypot(point[0] - (wall.p1[0] + t * dx), point[1] - (wall.p1[1] + t * dy))


def _cut_gap(wall: Segment, mid: tuple[float, float], width: float) -> list[Segment]:
    dx = wall.p2[0] - wall.p1[0]
    dy = wall.p2[1] - wall.p1[1]
    length = math.hypot(dx, dy)
    if length < 0.05:
        return []
    direction = (dx / length, dy / length)
    projection = (mid[0] - wall.p1[0]) * direction[0] + (mid[1] - wall.p1[1]) * direction[1]
    if projection < -0.2 or projection > length + 0.2:
        return [wall]
    left = projection - width / 2.0
    right = projection + width / 2.0
    pieces = []
    if left > 0.05:
        end = min(left, length)
        pieces.append(
            Segment(
                wall.p1,
                (wall.p1[0] + direction[0] * end, wall.p1[1] + direction[1] * end),
                wall.thickness_m,
                wall.height_m,
            )
        )
    if right < length - 0.05:
        start = max(right, 0.0)
        pieces.append(
            Segment(
                (wall.p1[0] + direction[0] * start, wall.p1[1] + direction[1] * start),
                wall.p2,
                wall.thickness_m,
                wall.height_m,
            )
        )
    return pieces


def _room_edge_walls(faces: list[Polygon]) -> list[Segment]:
    walls: list[Segment] = []
    for face in faces:
        ring = list(face.exterior.coords)
        for start, end in zip(ring, ring[1:]):
            if (start[0] - end[0]) ** 2 + (start[1] - end[1]) ** 2 < 0.2**2:
                continue
            walls.append(Segment((float(start[0]), float(start[1])), (float(end[0]), float(end[1])), WALL_M))
    return walls


def _bridge_doors(faces: list[Polygon]) -> list[DoorOpening]:
    doors: list[DoorOpening] = []
    parent = list(range(len(faces)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def unite(left: int, right: int) -> None:
        parent[find(left)] = find(right)

    for left in range(len(faces)):
        for right in range(left + 1, len(faces)):
            if faces[left].distance(faces[right]) > 1.5:
                continue
            opening = _opening(faces[left], faces[right])
            if opening is None:
                continue
            opening.hits = [left, right]
            doors.append(opening)
            unite(left, right)
    # Each separated group gets an exit on its outer edge so a person can leave.
    groups: dict[int, list[int]] = {}
    for index in range(len(faces)):
        groups.setdefault(find(index), []).append(index)
    if groups:
        largest = max(groups.values(), key=len)
        for members in groups.values():
            if members is not largest and len(members) < 2:
                continue
            exit_door = _exit_for(faces, members)
            exit_door.hits = [members[0]]
            doors.append(exit_door)
    return doors


def _opening(left: Polygon, right: Polygon) -> DoorOpening | None:
    point_a, point_b = nearest_points(left.boundary, right.boundary)
    start = (float(point_a.x), float(point_a.y))
    end = (float(point_b.x), float(point_b.y))
    gap = ((start[0] - end[0]) ** 2 + (start[1] - end[1]) ** 2) ** 0.5
    if gap < 0.15:
        direction = _outward(left, start)
        end = (start[0] + direction[0] * 0.9, start[1] + direction[1] * 0.9)
    elif gap < 0.9:
        mid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        direction = _unit((end[0] - start[0], end[1] - start[1]))
        start = (mid[0] - direction[0] * 0.45, mid[1] - direction[1] * 0.45)
        end = (mid[0] + direction[0] * 0.45, mid[1] + direction[1] * 0.45)
    return DoorOpening(start, end, max(gap, 0.9))


def _exit_for(faces: list[Polygon], members: list[int]) -> DoorOpening:
    union = faces[members[0]]
    for index in members[1:]:
        union = union.union(faces[index])
    if union.geom_type == "MultiPolygon":
        union = max(union.geoms, key=lambda part: part.area)
    centroid = union.centroid
    ring = list(union.exterior.coords)
    best = ring[0]
    best_distance = -1.0
    for point in ring:
        distance = (point[0] - centroid.x) ** 2 + (point[1] - centroid.y) ** 2
        if distance > best_distance:
            best = point
            best_distance = distance
    outward = _unit((best[0] - centroid.x, best[1] - centroid.y))
    start = (float(best[0]), float(best[1]))
    end = (start[0] + outward[0] * 0.9, start[1] + outward[1] * 0.9)
    door = DoorOpening(start, end, 0.9, is_exit=True, label="Exit")
    return door


def _outward(face: Polygon, point: tuple[float, float]) -> tuple[float, float]:
    center = face.centroid
    return _unit((point[0] - center.x, point[1] - center.y))


def _unit(vector: tuple[float, float]) -> tuple[float, float]:
    length = (vector[0] ** 2 + vector[1] ** 2) ** 0.5
    if length < 1e-6:
        return (1.0, 0.0)
    return (vector[0] / length, vector[1] / length)
