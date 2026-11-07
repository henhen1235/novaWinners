"""Procedural office plans with a matching ground-truth scene.

The PDF is what the pipeline reads. ``truth.scene.json`` is built from the same
meter coordinates, not from the pipeline output. Fixed seeds are deterministic.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from export.assemble import assemble_scene  # noqa: E402
from geometry.rooms import attach_doors, faces_from_walls, label_faces  # noqa: E402
from ingest_blueprint.types import DoorOpening, Segment  # noqa: E402

PT_PER_M = 36.0
KAPPA = 0.5522847498


def build_spec(seed: int) -> dict:
    rng = random.Random(seed)
    count = rng.randint(2, 3)
    widths = [round(rng.uniform(3.6, 4.6), 2) for _ in range(count)]
    top_depth = round(rng.uniform(3.5, 4.2), 2)
    bot_depth = round(rng.uniform(3.5, 4.2), 2)
    corridor_w = round(rng.uniform(2.0, 2.3), 2)
    door_w = 0.9
    asymmetric = seed % 2 == 1
    double_line = seed % 3 == 0
    has_stair = seed % 4 == 0
    scale_mode = "door" if seed % 7 == 0 else "scale_bar"
    xs = [0.0]
    for width in widths:
        xs.append(round(xs[-1] + width, 2))
    z0, z1 = 0.0, top_depth
    z2 = round(top_depth + corridor_w, 2)
    z3 = round(z2 + bot_depth, 2)
    annex = 3.6 if asymmetric else 0.0
    x_left = -annex

    walls: list[Segment] = []
    doors: list[DoorOpening] = []

    def add_gapped(p1, p2, gaps: list[tuple[float, float, bool, str, int]]) -> None:
        length = math.dist(p1, p2)
        if length < 0.05:
            return
        direction = ((p2[0] - p1[0]) / length, (p2[1] - p1[1]) / length)

        def at(distance: float) -> tuple[float, float]:
            return (p1[0] + direction[0] * distance, p1[1] + direction[1] * distance)

        cursor = 0.0
        for center, is_exit, label, swing in sorted(gaps, key=lambda item: item[0]):
            a = center - door_w / 2.0
            b = center + door_w / 2.0
            a = max(0.05, a)
            b = min(length - 0.05, b)
            if a - cursor >= 0.05:
                walls.append(Segment(at(cursor), at(a), 0.2))
            opening = DoorOpening(at(a), at(b), b - a, is_exit=is_exit, label=label, swing=swing)
            doors.append(opening)
            cursor = b
        if length - cursor >= 0.05:
            walls.append(Segment(at(cursor), at(length), 0.2))

    # Top edge, including the annex.
    add_gapped((x_left, z0), (xs[-1], z0), [])
    # Bottom edge of the main block.
    add_gapped((0.0, z3), (xs[-1], z3), [])
    # Corridor / top-room wall, with a door per top room. Annex bottom is solid.
    top_gaps = []
    for index in range(count):
        center = (xs[index] + xs[index + 1]) / 2.0 - x_left
        # This wall starts at x_left, so distance along the wall is x - x_left.
        top_gaps.append((center, False, "Door", -1))
    # Recompute centers in the wall's own frame. Wall starts at x_left.
    top_gaps = [(((xs[i] + xs[i + 1]) / 2.0) - x_left, False, "Door", -1) for i in range(count)]
    add_gapped((x_left, z1), (xs[-1], z1), top_gaps)
    bot_gaps = [(((xs[i] + xs[i + 1]) / 2.0) - 0.0, False, "Door", 1) for i in range(count)]
    add_gapped((0.0, z2), (xs[-1], z2), bot_gaps)

    # Left side. With an annex the left boundary is only beside the annex;
    # the corridor and lower rooms still close on x = 0.
    if asymmetric:
        add_gapped((x_left, z0), (x_left, z1), [])
        # Door between annex and Office 1, on x = 0.
        annex_door_center = z1 / 2.0
        add_gapped((0.0, z0), (0.0, z1), [(annex_door_center, False, "Door", 1)])
    else:
        add_gapped((0.0, z0), (0.0, z1), [])
    # Corridor left exit and lower-left wall.
    exit_center = (z2 - z1) / 2.0
    add_gapped((0.0, z1), (0.0, z2), [(exit_center, True, "West Exit", 0)])
    add_gapped((0.0, z2), (0.0, z3), [])
    # Right side with an east exit in the corridor.
    add_gapped((xs[-1], z0), (xs[-1], z1), [])
    add_gapped((xs[-1], z1), (xs[-1], z2), [((z2 - z1) / 2.0, True, "East Exit", 0)])
    add_gapped((xs[-1], z2), (xs[-1], z3), [])
    # Interior dividers.
    for x_value in xs[1:-1]:
        add_gapped((x_value, z0), (x_value, z1), [])
        add_gapped((x_value, z2), (x_value, z3), [])

    texts: list[tuple[str, tuple[float, float]]] = []
    for index in range(count):
        name = "Stair" if has_stair and index == count - 1 else f"Office {index + 1}"
        texts.append((name, ((xs[index] + xs[index + 1]) / 2.0, z1 / 2.0)))
        bot_name = f"Office {count + index + 1}"
        texts.append((bot_name, ((xs[index] + xs[index + 1]) / 2.0, (z2 + z3) / 2.0)))
    texts.append(("Corridor", (xs[-1] / 2.0, (z1 + z2) / 2.0)))
    if asymmetric:
        texts.append(("West Annex", (x_left / 2.0, z1 / 2.0)))

    return {
        "seed": seed,
        "name": f"Synthetic office {seed}",
        "walls": walls,
        "doors": doors,
        "texts": texts,
        "double_line": double_line,
        "scale_mode": scale_mode,
        "asymmetric": asymmetric,
        "has_stair": has_stair,
        "bounds": (x_left, z0, xs[-1], z3),
        "room_width_m": widths[0],
    }


def truth_scene(spec: dict) -> dict:
    faces = faces_from_walls(spec["walls"], spec["doors"])
    labels = label_faces(faces, [(text, anchor, 12.0) for text, anchor in spec["texts"]])
    stair_indexes = {index for index, (_name, room_type) in enumerate(labels) if room_type == "stairwell"}
    attached = attach_doors(spec["doors"], faces)
    doors = []
    for door, hits in attached:
        door.hits = hits
        if hits:
            doors.append(door)
    level = {
        "level_id": "L1",
        "name": "Ground floor",
        "elevation_m": 0.0,
        "ceiling_height_m": 2.7,
        "walls": spec["walls"],
        "doors": doors,
        "faces": faces,
        "labels": labels,
        "obstacles": [],
        "stair_indexes": stair_indexes,
    }
    scene, _walk = assemble_scene(
        [level],
        meta_name=spec["name"],
        source_file=f"synthetic/{spec['seed']}.pdf",
        scale_method="scale_bar" if spec["scale_mode"] == "scale_bar" else "door_reference",
        scale_confidence=0.95 if spec["scale_mode"] == "scale_bar" else 0.55,
        generated_by="tools/make_synthetic_plan.py",
        source="synthetic",
    )
    scene["extensions"]["seed"] = spec["seed"]
    scene["extensions"]["asymmetric"] = spec["asymmetric"]
    return scene


def render_pdf(spec: dict, path: Path) -> None:
    bounds = _drawing_bounds(spec)
    minx, minz, maxx, maxz = bounds
    pad = 0.4
    origin = (minx - pad, minz - pad)
    width = (maxx - minx + 2 * pad) * PT_PER_M
    height = (maxz - minz + 2 * pad) * PT_PER_M
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    shape = page.new_shape()

    def pdf_point(point: tuple[float, float]) -> fitz.Point:
        return fitz.Point((point[0] - origin[0]) * PT_PER_M, (point[1] - origin[1]) * PT_PER_M)

    for wall in spec["walls"]:
        _draw_wall(shape, wall, pdf_point, spec["double_line"])
    for door in spec["doors"]:
        if door.swing != 0:
            _draw_swing(shape, door, pdf_point)
    _draw_distractors(shape, page, spec, pdf_point)
    shape.commit()
    document.save(path)
    document.close()


def render_png(pdf_path: Path, png_path: Path, seed: int, distort: bool) -> None:
    import cv2
    import numpy as np

    document = fitz.open(pdf_path)
    page = document[0]
    zoom = 200 / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n).copy()
    document.close()
    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    if distort:
        rng = random.Random(seed)
        angle = rng.uniform(-3.0, 3.0)
        height, width = image.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        image = cv2.warpAffine(image, matrix, (width, height), borderValue=(255, 255, 255))
        image = cv2.GaussianBlur(image, (3, 3), 0.6)
        ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    cv2.imwrite(str(png_path), image)


def write_plan(seed: int, directory: Path, raster: bool = False, distort: bool = False) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    spec = build_spec(seed)
    render_pdf(spec, directory / "plan.pdf")
    scene = truth_scene(spec)
    (directory / "truth.scene.json").write_text(json.dumps(scene, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if raster:
        render_png(directory / "plan.pdf", directory / "plan.png", seed, distort)
    return scene


def _drawing_bounds(spec: dict) -> tuple[float, float, float, float]:
    xs = [point[0] for wall in spec["walls"] for point in (wall.p1, wall.p2)]
    zs = [point[1] for wall in spec["walls"] for point in (wall.p1, wall.p2)]
    minx, maxx = min(xs), max(xs)
    minz, maxz = min(zs), max(zs)
    if spec["scale_mode"] == "scale_bar":
        minz -= 1.6
        maxx = max(maxx, minx + 5.5)
    else:
        minz -= 0.4
    maxz += 1.8
    return minx, minz, maxx, maxz


def _draw_wall(shape, wall: Segment, pdf_point, double_line: bool) -> None:
    if not double_line:
        shape.draw_line(pdf_point(wall.p1), pdf_point(wall.p2))
        shape.finish(color=(0, 0, 0), width=wall.thickness_m * PT_PER_M, closePath=False)
        return
    direction = _unit(wall.p1, wall.p2)
    normal = (-direction[1], direction[0])
    half = wall.thickness_m / 2.0
    for sign in (-1.0, 1.0):
        start = _extend(wall.p1, wall.p2, normal, sign * half, 0.02)
        end = _extend(wall.p2, wall.p1, normal, sign * half, 0.02)
        shape.draw_line(pdf_point(start), pdf_point(end))
        shape.finish(color=(0, 0, 0), width=1.0, closePath=False)


def _extend(start, away, normal, offset, extra) -> tuple[float, float]:
    direction = _unit(away, start) if False else _unit(start, away)
    # `away` is the other endpoint. Move `extra` past `start`, away from it? Caller passes
    # start=p1, away=p2 for the first point, so the extension direction is from p2 toward p1.
    outward = _unit(away, start)
    return (
        start[0] + normal[0] * offset + outward[0] * extra,
        start[1] + normal[1] * offset + outward[1] * extra,
    )


def _draw_swing(shape, door: DoorOpening, pdf_point) -> None:
    vector = (door.p2[0] - door.p1[0], door.p2[1] - door.p1[1])
    if door.swing > 0:
        turned = (-vector[1], vector[0])
    else:
        turned = (vector[1], -vector[0])
    open_pt = (door.p1[0] + turned[0], door.p1[1] + turned[1])
    closed = door.p2
    hinge = door.p1
    a = (closed[0] - hinge[0], closed[1] - hinge[1])
    b = (open_pt[0] - hinge[0], open_pt[1] - hinge[1])
    p0 = closed
    p3 = open_pt
    p1 = (closed[0] + b[0] * KAPPA, closed[1] + b[1] * KAPPA)
    p2 = (open_pt[0] + a[0] * KAPPA, open_pt[1] + a[1] * KAPPA)
    shape.draw_bezier(pdf_point(p0), pdf_point(p1), pdf_point(p2), pdf_point(p3))
    shape.finish(color=(0, 0, 0), width=1.0, closePath=False)
    shape.draw_line(pdf_point(hinge), pdf_point(open_pt))
    shape.finish(color=(0, 0, 0), width=1.0, closePath=False)


def _draw_distractors(shape, page, spec: dict, pdf_point) -> None:
    minx, minz, maxx, maxz = spec["bounds"]
    gray = (0.62, 0.62, 0.62)
    # Hatch in the lower-left room.
    for step in range(6):
        x0 = minx + 0.4 + step * 0.25
        shape.draw_line(pdf_point((x0, maxz - 0.4)), pdf_point((x0 + 0.8, maxz - 1.2)))
        shape.finish(color=gray, width=0.4, closePath=False)
    # Furniture rectangle.
    fx, fz = minx + 1.0, minz + 1.0
    for p1, p2 in (
        ((fx, fz), (fx + 1.2, fz)),
        ((fx + 1.2, fz), (fx + 1.2, fz + 0.6)),
        ((fx + 1.2, fz + 0.6), (fx, fz + 0.6)),
        ((fx, fz + 0.6), (fx, fz)),
    ):
        shape.draw_line(pdf_point(p1), pdf_point(p2))
        shape.finish(color=gray, width=0.5, closePath=False)
    if spec["scale_mode"] == "scale_bar":
        bar_z = minz - 1.1
        shape.draw_line(pdf_point((minx, bar_z)), pdf_point((minx + 5.0, bar_z)))
        shape.finish(color=(0, 0, 0), width=1.1, closePath=False)
        page.insert_text(pdf_point((minx, bar_z + 0.45)), "0  5 m", fontsize=11, color=(0, 0, 0))
    # Dimension under the first room. Omitted in door-reference plans so it cannot set the scale.
    if spec["scale_mode"] != "door":
        dim_z = maxz + 0.9
        width = spec["room_width_m"]
        shape.draw_line(pdf_point((minx, dim_z)), pdf_point((minx + width, dim_z)))
        shape.finish(color=(0, 0, 0), width=0.7, closePath=False)
        page.insert_text(pdf_point((minx, dim_z + 0.35)), str(int(round(width * 1000))), fontsize=9, color=(0, 0, 0))
    for text, anchor in spec["texts"]:
        page.insert_text(pdf_point((anchor[0] - 0.4, anchor[1])), text, fontsize=10, color=(0, 0, 0))
    # Exit tags sit just outside the exits.
    for door in spec["doors"]:
        if not door.is_exit:
            continue
        page.insert_text(pdf_point((door.midpoint[0] - 0.3, door.midpoint[1])), "EXIT", fontsize=9, color=(0, 0, 0))


def _unit(p1, p2) -> tuple[float, float]:
    length = math.dist(p1, p2) or 1.0
    return ((p2[0] - p1[0]) / length, (p2[1] - p1[1]) / length)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic floor plans")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--raster", action="store_true")
    parser.add_argument("--distort", action="store_true")
    args = parser.parse_args()
    for offset in range(args.count):
        seed = args.seed + offset
        directory = args.out / f"seed_{seed:03d}" if args.count > 1 else args.out
        write_plan(seed, directory, raster=args.raster, distort=args.distort)
        print(directory)


if __name__ == "__main__":
    main()
