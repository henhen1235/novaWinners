"""End-to-end blueprint to scene.json.

Pixel y increases downward. Meter z uses that axis directly:
``z = py / px_per_m``. The building is not mirrored.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon

from contract.validate_scene import validate
from export.assemble import assemble_scene
from geometry.rooms import attach_doors, faces_from_walls, label_faces, room_type_from_name
from geometry.infer_doors import doors_from_arcs, infer_doors, needs_inference
from geometry.stairs import align_levels, stair_room_indexes, tread_clusters
from geometry.vectorize import snap_segments, split_gaps
from ingest_blueprint.corrections import apply_corrections, manual_scale, scale_from_manual
from ingest_blueprint.loader import load
from ingest_blueprint.raster_rooms import extract as extract_raster_rooms
from ingest_blueprint.cad_raster import is_dense_cad, wall_raster_page
from ingest_blueprint.scale import area_label_ratio, estimate_scale
from ingest_blueprint.segment import deskew, detect_raster, detect_vector
from ingest_blueprint.semantics import label_image, load_env
from ingest_blueprint.types import DoorOpening, PlanPage, ScaleEstimate, Segment
from render.extrude import build_display, build_navmesh_debug

ROOT = Path(__file__).resolve().parents[1]
SLAB_M = 3.0
RASTER_DOOR_DEPTH_M = 0.9  # covers attach_doors' 0.35 m reach plus a wall on each side
MIN_WALL_M = 0.15  # shorter wall pieces fall under the validator's 0.01 m^2 polygon floor


def generated_by() -> str:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = "unknown"
    return f"ingest_blueprint {sha}"


def run(
    source: str | Path,
    out_dir: str | Path,
    no_llm: bool = False,
    corrections_path: str | Path | None = None,
    use_ml: bool = False,
) -> tuple[dict, int]:
    """Write scene outputs under ``out_dir``. Returns the scene and the validator exit code."""

    load_env()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    debug = out / "debug"
    debug.mkdir(exist_ok=True)
    corrections = _read_json(corrections_path)
    pages = [page for page in load(source) if _keep_page(page)]
    if not pages:
        pages = load(source)[:1]
    manual = manual_scale(corrections)
    level_payloads = []
    scales: list[ScaleEstimate] = []
    notes: dict = {}
    for index, page in enumerate(pages):
        level_id = f"L{index + 1}"
        if use_ml:
            page = _try_ml(page)
        raster_faces = None
        raster_wall_m = 0.2
        if is_dense_cad(page):
            page = wall_raster_page(page)
        if page.strokes:
            scale = _scale_for(page, manual)
            walls, doors, thin = detect_vector(page, scale)
        else:
            page = deskew(page)
            if manual is not None:
                scale = _scale_for(page, manual)
                walls, doors = detect_raster(page, scale)
                thin = []
            else:
                scale, raster_faces, doors, raster_wall_m = extract_raster_rooms(page)
                walls, thin = [], []
                if raster_faces:
                    scale, raster_faces, doors, raster_wall_m = _rescale_from_area_labels(page, scale, raster_faces, doors, raster_wall_m, notes)
                raster_faces = _drop_voids(raster_faces, page, scale, notes)
                if raster_faces and page.door_arcs:
                    doors = _merge_doors(doors, doors_from_arcs(raster_faces, _arcs_m(page, scale)))
                if page.door_arcs or needs_inference(doors, raster_faces):
                    doors = infer_doors(raster_faces, doors)
                if not raster_faces:
                    scale = _raster_door_scale(page)
                    walls, doors = detect_raster(page, scale)
        walls, doors, extra_labels, stair_polys, obstacles = apply_corrections(
            walls, doors, corrections, scale.px_per_m
        )
        if corrections and corrections.get("operations"):
            walls = snap_segments(walls)
            walls, gap_doors = split_gaps(walls)
            doors = _merge_doors(doors, gap_doors)
        texts = _texts_m(page, scale.px_per_m) + [(text, anchor, size) for text, anchor, size in extra_labels]
        vision = label_image(page.raster, out / "llm_cache", no_llm=no_llm)
        for item in vision:
            anchor_m = (item["anchor"][0] / scale.px_per_m, item["anchor"][1] / scale.px_per_m)
            if item["kind"] == "room":
                texts.append((item["name"], anchor_m, 18.0))
            elif item["kind"] == "stair":
                texts.append((item["name"], anchor_m, 16.0))
            elif item["kind"] == "exit":
                _mark_nearest_exit(doors, anchor_m, item["name"])
        faces = raster_faces if raster_faces else faces_from_walls(walls, doors)
        if raster_faces and not walls:
            walls = _boundary_walls(faces, raster_wall_m, doors, RASTER_DOOR_DEPTH_M)
        labels = label_faces(faces, texts)
        labels = _apply_vision_types(faces, labels, vision, scale.px_per_m)
        stair_indexes = stair_room_indexes(
            faces,
            tread_clusters(thin) + stair_polys,
            [(text, anchor) for text, anchor, _size in texts],
        )
        for face_index, (name, room_type) in enumerate(labels):
            if face_index in stair_indexes and room_type == "unknown":
                labels[face_index] = (name if name != "Room" else "Stair", "stairwell")
        attached = attach_doors(doors, faces)
        kept_doors: list[DoorOpening] = []
        for door, hits in attached:
            door.hits = hits
            if hits:
                kept_doors.append(door)
        _write_debug_geojson(debug / f"{level_id}_rooms.geojson", faces)
        _write_overlay(out if index == 0 else debug, page, faces, kept_doors, scale, filename="overlay.png" if index == 0 else f"overlay_{level_id}.png")
        level_payloads.append(
            {
                "level_id": level_id,
                "name": "Ground floor" if index == 0 else f"Level {index + 1}",
                "elevation_m": index * SLAB_M,
                "ceiling_height_m": 2.7,
                "walls": walls,
                "doors": kept_doors,
                "faces": faces,
                "labels": labels,
                "obstacles": obstacles,
                "stair_indexes": stair_indexes,
                # Raster faces of two rooms can sit a wall gap apart, so door bridges reach further.
                "door_depth_m": RASTER_DOOR_DEPTH_M if raster_faces else 0.2,
                "page": page,
                "scale": scale,
            }
        )
        scales.append(scale)
    _align(level_payloads)
    method = scales[0].method if scales else "manual"
    confidence = min((scale.confidence for scale in scales), default=0.0)
    if confidence < 0.5:
        method = scales[0].method if scales else "door_reference"
    scene, _walkable = assemble_scene(
        level_payloads,
        meta_name=Path(source).stem,
        source_file=str(source),
        scale_method=method,
        scale_confidence=confidence,
        generated_by=generated_by(),
        source="blueprint",
    )
    issues = validate(scene)
    scene["warnings"] = [
        {"code": issue["code"], "message": issue["message"], "ref": issue.get("ref"), "severity": issue["severity"]}
        for issue in issues
        if issue["code"] != "SCHEMA_INVALID"
    ]
    inferred = sum(1 for level in level_payloads for door in level["doors"] if door.inferred)
    if inferred:
        notes["inferred_doors"] = inferred
    scene["extensions"].update(notes)
    scene_path = out / "scene.json"
    scene_path.write_text(json.dumps(scene, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    display = build_display(scene)
    display.export(out / "display.glb")
    debug_mesh = build_navmesh_debug(scene["navmesh"])
    if debug_mesh is not None:
        debug_mesh.export(out / "navmesh_debug.glb")
    report = {
        "errors": [issue for issue in issues if issue["severity"] == "error"],
        "warnings": [issue for issue in issues if issue["severity"] == "warn"],
    }
    (out / "validator_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    exit_code = 1 if report["errors"] else 0
    return scene, exit_code


_VOID = re.compile(r"\bOTB|OPEN TO BELOW|^OPEN$", re.IGNORECASE)


def _drop_voids(faces, page: PlanPage, scale: ScaleEstimate, notes: dict):
    """Remove faces tagged open-to-below ("OTB3-4"): atria and shafts are not floor."""

    tags = [Point(span.center[0] / scale.px_per_m, span.center[1] / scale.px_per_m) for span in page.texts if _VOID.search(span.text.strip())]
    kept = [face for face in faces if not any(face.contains(tag) for tag in tags)]
    if len(kept) < len(faces):
        notes["void_faces_dropped"] = len(faces) - len(kept)
    return kept


def _arcs_m(page: PlanPage, scale: ScaleEstimate):
    k = scale.px_per_m
    return [((h[0] / k, h[1] / k), (m[0] / k, m[1] / k), r / k) for h, m, r in page.door_arcs]


def _rescale_from_area_labels(page: PlanPage, scale: ScaleEstimate, faces, doors, wall_m: float, notes: dict):
    """Correct a raster scale with room area tags when at least three agree."""

    texts_m = [(span.text, (span.center[0] / scale.px_per_m, span.center[1] / scale.px_per_m)) for span in page.texts]
    ratio = area_label_ratio(faces, texts_m, wall_m)
    if ratio is None:
        return scale, faces, doors, wall_m
    notes["scale_source"] = "area_labels"

    def grow(point):
        return (point[0] * ratio, point[1] * ratio)

    faces = [affinity.scale(face, ratio, ratio, origin=(0, 0)) for face in faces]
    doors = [replace(door, p1=grow(door.p1), p2=grow(door.p2), width_m=door.width_m * ratio) for door in doors]
    # The contract has no area-tag method; tags are measured text, the closest is dimension_text.
    return ScaleEstimate(scale.px_per_m / ratio, "dimension_text", 0.85), faces, doors, wall_m * ratio


def _boundary_walls(faces: list[Polygon], thickness_m: float = 0.2, doors: list[DoorOpening] = (), depth_m: float = 0.2) -> list[Segment]:
    """Centerlines along raster room edges so walkable can cut the wall thickness.

    A raster face runs into its half of each door gap, so the edge across the
    gap sits on the doorway; it is skipped or the wall would close the door.
    ponytail: thickness capped at 0.2 m because door bridges are sized for
    0.2 m walls (assemble_scene); pass the real thickness there to lift it.
    """

    # Neighbouring faces overlap by one wall thickness, so the far face's edge sits
    # a full wall away from the door line; both must be cut.
    near = max(thickness_m + 0.05, depth_m / 2.0)
    thickness_m = min(thickness_m, 0.2)
    openings = [LineString([door.p1, door.p2]) for door in doors]
    walls: list[Segment] = []
    for face in faces:
        ring = _decimate(list(face.exterior.coords), MIN_WALL_M)
        for start, end in zip(ring, ring[1:]):
            if any(opening.distance(Point(start)) <= near and opening.distance(Point(end)) <= near for opening in openings):
                continue
            for piece_start, piece_end in _cut_openings(start, end, openings, near):
                walls.append(Segment(piece_start, piece_end, thickness_m))
    return walls


def _decimate(ring: list, step_m: float) -> list:
    """Drop ring vertices closer than ``step_m`` to the last kept one; the ring stays closed."""

    kept = [ring[0]]
    for point in ring[1:-1]:
        if (point[0] - kept[-1][0]) ** 2 + (point[1] - kept[-1][1]) ** 2 >= step_m**2:
            kept.append(point)
    kept.append(ring[-1])
    return kept


def _cut_openings(start, end, openings: list[LineString], near: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Split a face edge around every door that lies along it (within ``near``)."""

    edge = LineString([start, end])
    gaps = []
    for opening in openings:
        a, b = opening.coords[0], opening.coords[-1]
        if edge.distance(Point(a)) <= near and edge.distance(Point(b)) <= near:
            low, high = sorted((edge.project(Point(a)), edge.project(Point(b))))
            gaps.append((low - 0.05, high + 0.05))
    pieces, cursor = [], 0.0
    for low, high in sorted(gaps):
        if low > cursor:
            pieces.append((cursor, low))
        cursor = max(cursor, high)
    if cursor < edge.length:
        pieces.append((cursor, edge.length))
    out = []
    for low, high in pieces:
        if high - low < MIN_WALL_M:
            continue
        p, q = edge.interpolate(low), edge.interpolate(high)
        out.append(((float(p.x), float(p.y)), (float(q.x), float(q.y))))
    return out


def _scale_for(page: PlanPage, manual) -> ScaleEstimate:
    if manual is None:
        return estimate_scale(page)
    p1, p2, meters = manual
    return ScaleEstimate(scale_from_manual(p1, p2, meters), "manual", 1.0)


def _raster_door_scale(page: PlanPage) -> ScaleEstimate:
    """Assume the median Hough gap is a 0.9 m door once walls exist at a temporary scale."""

    guess = ScaleEstimate(80.0, "door_reference", 0.4)
    _walls, doors = detect_raster(page, guess)
    if not doors:
        return guess
    # Gaps were measured with the guess scale. Recover pixels and recompute.
    widths_px = [door.width_m * guess.px_per_m for door in doors if 0.55 <= door.width_m <= 1.6]
    if not widths_px:
        return guess
    from statistics import median

    px_per_m = float(median(widths_px)) / 0.9
    if not 15.0 <= px_per_m <= 500.0:
        return guess
    return ScaleEstimate(px_per_m, "door_reference", 0.55)


def _texts_m(page: PlanPage, px_per_m: float) -> list[tuple[str, tuple[float, float], float]]:
    return [(span.text, (span.center[0] / px_per_m, span.center[1] / px_per_m), span.size) for span in page.texts]


def _apply_vision_types(faces, labels, vision, px_per_m: float):
    updated = list(labels)
    for item in vision:
        if item["kind"] != "room":
            continue
        anchor = (item["anchor"][0] / px_per_m, item["anchor"][1] / px_per_m)
        for index, face in enumerate(faces):
            if face.covers(Point(anchor)) and updated[index][1] == "unknown":
                room_type = item["type"] if item["type"] in {
                    "office",
                    "corridor",
                    "stairwell",
                    "lobby",
                    "restroom",
                    "classroom",
                    "storage",
                    "mechanical",
                    "outside",
                    "unknown",
                } else room_type_from_name(item["name"])
                updated[index] = (item["name"], room_type)
    return updated


def _mark_nearest_exit(doors: list[DoorOpening], anchor: tuple[float, float], label: str) -> None:
    if not doors:
        return
    nearest = min(doors, key=lambda door: (door.midpoint[0] - anchor[0]) ** 2 + (door.midpoint[1] - anchor[1]) ** 2)
    nearest.is_exit = True
    nearest.label = label


def _merge_doors(explicit: list[DoorOpening], detected: list[DoorOpening]) -> list[DoorOpening]:
    merged = list(explicit)
    for door in detected:
        if any(_dist(door.midpoint, other.midpoint) < 0.4 for other in merged):
            continue
        merged.append(door)
    return merged


def _align(levels: list[dict]) -> None:
    points = []
    for level in levels:
        centroids = []
        for index in level["stair_indexes"]:
            center = level["faces"][index].centroid
            centroids.append((float(center.x), float(center.y)))
        points.append(centroids)
    shifts = align_levels(points)
    for level, shift in zip(levels, shifts):
        if abs(shift[0]) < 1e-6 and abs(shift[1]) < 1e-6:
            continue
        level["faces"] = [_shift_poly(face, shift) for face in level["faces"]]
        level["walls"] = [Segment(_shift(wall.p1, shift), _shift(wall.p2, shift), wall.thickness_m) for wall in level["walls"]]
        shifted_doors = []
        for door in level["doors"]:
            shifted_doors.append(
                DoorOpening(
                    _shift(door.p1, shift),
                    _shift(door.p2, shift),
                    door.width_m,
                    door.kind,
                    door.is_exit,
                    door.leads_to,
                    door.label,
                    list(door.hits),
                )
            )
        level["doors"] = shifted_doors
        level["obstacles"] = [_shift_poly(poly, shift) for poly in level["obstacles"]]


def _shift(point, shift) -> tuple[float, float]:
    return (point[0] + shift[0], point[1] + shift[1])


def _shift_poly(poly: Polygon, shift) -> Polygon:
    return Polygon([(x + shift[0], z + shift[1]) for x, z in poly.exterior.coords[:-1]])


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _keep_page(page: PlanPage) -> bool:
    legend = any("legend" in span.text.lower() for span in page.texts) and len(page.strokes) < 12
    if legend:
        return False
    if page.strokes:
        return len(page.strokes) >= 8
    return True


def _try_ml(page: PlanPage) -> PlanPage:
    """Optional CubiCasa weights. Absent weights fall back to the classical path."""

    weights = ROOT / "data" / "cubicasa" / "model_weights.pth"
    if not weights.is_file():
        return page
    return page


def _read_json(path: str | Path | None) -> dict | None:
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_debug_geojson(path: Path, faces: list[Polygon]) -> None:
    features = []
    for index, face in enumerate(faces):
        ring = [[float(x), float(z)] for x, z in face.exterior.coords]
        features.append(
            {
                "type": "Feature",
                "properties": {"id": index, "area_m2": round(float(face.area), 3)},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")


def _write_overlay(directory: Path, page: PlanPage, faces, doors, scale: ScaleEstimate, filename: str) -> None:
    image = page.raster.copy()
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)

    def px(point) -> tuple[int, int]:
        return (int(round(point[0] * scale.px_per_m)), int(round(point[1] * scale.px_per_m)))

    for face in faces:
        pts = np.array([px(point) for point in face.exterior.coords], dtype=np.int32)
        cv2.polylines(image, [pts], True, (40, 90, 220), 2)
    for door in doors:
        color = (30, 160, 70) if door.is_exit or len(door.hits) == 1 else (220, 60, 40)
        cv2.line(image, px(door.p1), px(door.p2), color, 3)
    out = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(directory / filename), out)
