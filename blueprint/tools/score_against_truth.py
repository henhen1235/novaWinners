"""Score a predicted scene against a synthetic ground-truth scene.

Matching is translation-invariant: both walkable polygons are shifted so their
centroids coincide. Door midpoints use that same shift and match within 0.5 m.
Scale error is the relative error of the walkable bounding-box width.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from shapely.affinity import translate
from shapely.geometry import Polygon
from shapely.ops import unary_union

DOOR_MATCH_M = 0.5
EXIT_MATCH_M = 1.0


def walkable_geometry(scene: dict) -> Polygon:
    parts = []
    for payload in scene["walkable"].values():
        for item in payload["polygons"]:
            holes = item.get("holes") or []
            polygon = Polygon(item["exterior"], holes)
            if not polygon.is_empty and polygon.area > 0:
                parts.append(polygon)
    if not parts:
        return Polygon()
    merged = unary_union(parts)
    return merged


def _shift_to(source, target) -> tuple[float, float]:
    if source.is_empty or target.is_empty:
        return (0.0, 0.0)
    return (target.centroid.x - source.centroid.x, target.centroid.y - source.centroid.y)


def _iou(left, right) -> float:
    if left.is_empty and right.is_empty:
        return 1.0
    union = left.union(right)
    if union.is_empty or union.area <= 0:
        return 0.0
    return float(left.intersection(right).area / union.area)


def _midpoint(segment: list) -> tuple[float, float]:
    a, b = segment
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _match(predicted: list[tuple[float, float]], truth: list[tuple[float, float]], radius: float) -> tuple[int, int, int]:
    """Return (true positives, predicted count, truth count) with greedy matching."""

    used: set[int] = set()
    hits = 0
    for point in predicted:
        best = None
        best_distance = radius
        for index, other in enumerate(truth):
            if index in used:
                continue
            distance = ((point[0] - other[0]) ** 2 + (point[1] - other[1]) ** 2) ** 0.5
            if distance <= best_distance:
                best = index
                best_distance = distance
        if best is not None:
            used.add(best)
            hits += 1
    return hits, len(predicted), len(truth)


def _width(geom) -> float:
    if geom.is_empty:
        return 0.0
    minx, _miny, maxx, _maxy = geom.bounds
    return float(maxx - minx)


def score_scenes(predicted: dict, truth: dict) -> dict:
    pred_walk = walkable_geometry(predicted)
    truth_walk = walkable_geometry(truth)
    dx, dz = _shift_to(pred_walk, truth_walk)
    aligned = translate(pred_walk, xoff=dx, yoff=dz) if not pred_walk.is_empty else pred_walk
    iou = _iou(aligned, truth_walk)

    def moved(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] + dx, point[1] + dz)

    pred_doors = [moved(_midpoint(door["segment"])) for door in predicted["doors"]]
    truth_doors = [_midpoint(door["segment"]) for door in truth["doors"]]
    door_hits, door_pred, door_truth = _match(pred_doors, truth_doors, DOOR_MATCH_M)
    pred_exits = [moved((exit_["position"][0], exit_["position"][1])) for exit_ in predicted["exits"]]
    truth_exits = [(exit_["position"][0], exit_["position"][1]) for exit_ in truth["exits"]]
    exit_hits, _exit_pred, exit_truth = _match(pred_exits, truth_exits, EXIT_MATCH_M)

    truth_width = _width(truth_walk)
    pred_width = _width(pred_walk)
    scale_error = abs(pred_width - truth_width) / truth_width if truth_width > 0 else 1.0
    unreachable = any(
        warning["code"] == "ROOM_UNREACHABLE" for warning in predicted.get("warnings", [])
    )
    return {
        "walkable_iou": round(iou, 4),
        "door_recall": round(door_hits / door_truth, 4) if door_truth else 1.0,
        "door_precision": round(door_hits / door_pred, 4) if door_pred else 1.0,
        "door_hits": door_hits,
        "door_predicted": door_pred,
        "door_truth": door_truth,
        "room_delta": len(predicted["rooms"]) - len(truth["rooms"]),
        "exit_recall": round(exit_hits / exit_truth, 4) if exit_truth else 1.0,
        "scale_error": round(scale_error, 4),
        "scale_method": predicted["meta"]["scale_method"],
        "connected": (not unreachable) and bool(predicted.get("exits")),
        "shift_m": [round(dx, 4), round(dz, 4)],
    }


def load_scene(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a scene against ground truth")
    parser.add_argument("truth")
    parser.add_argument("predicted")
    args = parser.parse_args()
    result = score_scenes(load_scene(Path(args.predicted)), load_scene(Path(args.truth)))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
