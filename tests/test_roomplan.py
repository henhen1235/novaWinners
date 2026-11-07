"""Path A: RoomPlan JSON -> scene.json on the samples in samples/roomplan (see FORMAT.md)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon

from ingest_lidar.pipeline import run
from ingest_lidar.roomplan_to_scene import convert

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "roomplan"
HANDMADE = SAMPLES / "handmade_structure.json"
MYHOME = SAMPLES / "apple_myhome"


def _errors(scene: dict) -> list[dict]:
    return [i for i in scene["extensions"]["lidar"]["validation"] if i["severity"] == "error"]


@pytest.mark.parametrize("path, walls, rooms", [
    (HANDMADE, 7, 2),
    (MYHOME / "Bathroom1" / "capturedRoom.json", 9, 1),
    (MYHOME, 67, 11),  # folder of 11 rooms from one ARSession
    (SAMPLES / "exealabs_kitchen" / "kitchen_real_01.json", 4, 1),
])
def test_sample_converts_and_validates(tmp_path: Path, path: Path, walls: int, rooms: int) -> None:
    scene = convert(path, tmp_path)
    assert _errors(scene) == []
    assert len(scene["walls"]) == walls
    assert len(scene["rooms"]) == rooms
    assert scene["exits"]
    for name in ("scene.json", "validation.json", "display.glb", "navmesh_debug.glb", "debug/overview.png"):
        assert (tmp_path / name).exists(), name


def test_handmade_structure_details(tmp_path: Path) -> None:
    scene = convert(HANDMADE, tmp_path)
    assert scene["levels"][0]["elevation_m"] == 0.0
    by_kind = sorted((d["kind"], "outside" in d["connects"]) for d in scene["doors"])
    assert by_kind == [("opening", True), ("swing", False), ("swing", True)]
    interior = next(d for d in scene["doors"] if "outside" not in d["connects"])
    assert np.allclose(np.mean(interior["segment"], axis=0), [5.0, 1.0], atol=0.01)
    assert len(scene["stairs"]) == 1 and len(scene["obstacles"]) == 2
    assert any(w["code"] == "ORPHAN_STAIR" for w in scene["warnings"])
    assert [r["has_window"] for r in scene["rooms"]] == [True, False]


def test_not_mirrored(tmp_path: Path) -> None:
    """x and z pass through: the door and floor polygon of an asymmetric room land where RoomPlan put them."""
    src = MYHOME / "Bedroom4" / "capturedRoom.json"
    data = json.loads(src.read_text())
    scene = convert(src, tmp_path)
    m = np.asarray(data["floors"][0]["transform"], float).reshape(4, 4).T
    floor_in = Polygon([(m @ [c[0], c[1], 0, 1])[[0, 2]] for c in data["floors"][0]["polygonCorners"]])
    floor_out = Polygon(scene["rooms"][0]["polygon"])
    assert floor_in.symmetric_difference(floor_out).area < 0.01
    door_in = [np.asarray(d["transform"], float)[[12, 14]] for d in data["doors"]]
    door_out = [np.mean(d["segment"], axis=0) for d in scene["doors"] if d["kind"] == "swing"]
    for p in door_in:
        assert min(np.hypot(*(q - p)) for q in door_out) < 0.02


def test_exit_tags_override(tmp_path: Path) -> None:
    shutil.copy(HANDMADE, tmp_path / "structure.json")
    (tmp_path / "exit_tags.json").write_text(json.dumps([[1.2, 0.0, "Front door"]]))
    scene = convert(tmp_path / "structure.json", tmp_path / "out")
    assert [e["label"] for e in scene["exits"]] == ["Front door"]
    assert _errors(scene) == []


def test_pipeline_dispatches_json(tmp_path: Path) -> None:
    scene = run(HANDMADE, tmp_path)
    assert scene["meta"]["scale_method"] == "arkit_metric" and _errors(scene) == []
