"""Rooms from area tags printed on a CAD sheet, such as ``A115`` / ``806sf``.

The line tracer treats furniture and dimensions as walls. These sheets already
name each room and give its area, and the white space inside the walls is the
room. Scale comes from that printed area, not from a guessed door width.
"""

from __future__ import annotations

import re
from pathlib import Path

import cv2
import fitz
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import nearest_points

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


def read_area_plans(source: str | Path, out_dir: str | Path) -> dict | None:
    """Build a scene when the PDF pages carry room-area tags. ``None`` otherwise."""

    path = Path(source)
    if path.suffix.lower() != ".pdf":
        return None
    document = fitz.open(path)
    try:
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
        meta_name=_building_name(path),
        source_file=str(path),
        scale_method="dimension_text",
        scale_confidence=0.75,
        generated_by=generated_by(),
        source="blueprint",
    )
    scene, code = publish_scene(scene, out_dir)
    return {
        "scene": scene,
        "exit_code": code,
        "building": _building_name(path),
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
    walls, doors = _walls_and_doors(faces)
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
        "stair_indexes": stair_indexes,
        "px_per_m": ppm,
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


def _walls_and_doors(faces: list[Polygon]) -> tuple[list[Segment], list[DoorOpening]]:
    walls: list[Segment] = []
    for face in faces:
        ring = list(face.exterior.coords)
        for start, end in zip(ring, ring[1:]):
            if (start[0] - end[0]) ** 2 + (start[1] - end[1]) ** 2 < 0.2**2:
                continue
            walls.append(Segment((float(start[0]), float(start[1])), (float(end[0]), float(end[1])), WALL_M))
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
    walls = cut_walls_for_doors(walls, doors)
    return walls, doors


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
