"""Path A: RoomPlan JSON -> scene.json on the samples in samples/roomplan (see FORMAT.md)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from ingest_lidar.pipeline import run
from ingest_lidar.roomplan_align import rigid
from ingest_lidar.roomplan_to_scene import _floor_polygon, convert

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "roomplan"
HANDMADE = SAMPLES / "handmade_structure.json"
MYHOME = SAMPLES / "apple_myhome"
HEYDAY = SAMPLES / "heyday"
MYHOME_ROOMS = sorted(p.name for p in MYHOME.iterdir() if p.is_dir())


def _errors(scene: dict) -> list[dict]:
    return [i for i in scene["extensions"]["lidar"]["validation"] if i["severity"] == "error"]


@pytest.mark.parametrize("path, walls, rooms", [
    (HANDMADE, 7, 2),
    (MYHOME / "Bathroom1" / "capturedRoom.json", 9, 1),
    (MYHOME, 66, 11),  # folder of 11 rooms from one ARSession; Hallway1/2 both scanned one wall
    (SAMPLES / "exealabs_kitchen" / "kitchen_real_01.json", 4, 1),
    (HEYDAY / "open_plan_living_dining_kitchen.json", 22, 1),  # open plan stays one room
    (HEYDAY / "apartment_21_walls.json", 21, 1),
    (HEYDAY / "living_room_7_walls.json", 7, 1),
    (HEYDAY / "living_room_two_sections.json", 8, 1),
    (HEYDAY / "partial_2_walls.json", 2, 1),  # walls do not close; the floor polygon still does
] + [(MYHOME / name / "capturedRoom.json", None, 1) for name in MYHOME_ROOMS])
def test_sample_converts_and_validates(tmp_path: Path, path: Path, walls: int, rooms: int) -> None:
    scene = convert(path, tmp_path)
    assert _errors(scene) == []
    assert walls is None or len(scene["walls"]) == walls
    assert len(scene["rooms"]) == rooms
    assert scene["exits"]
    # walkable covers each room's floor minus furniture (both eroded by the 0.25 m agent radius)
    walk = unary_union([Polygon(p["exterior"], p["holes"]) for w in scene["walkable"].values() for p in w["polygons"]])
    blocked = unary_union([Polygon(o["polygon"]) for o in scene["obstacles"] if not o["passable"]] +
                          [Polygon(w["polygon"]) for w in scene["walls"]] + [Polygon(s["polygon"]) for s in scene["stairs"]])
    for r in scene["rooms"]:
        free = Polygon(r["polygon"]).difference(blocked).buffer(-0.25)
        if free.area > 0.5:
            assert walk.intersection(free).area > 0.9 * free.area, r["name"]
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


def _rooms_dir(tmp_path: Path, names: list[str]) -> Path:
    d = tmp_path / "scan"
    d.mkdir()
    for i, name in enumerate(names):
        shutil.copy(MYHOME / name / "capturedRoom.json", d / f"room_{i}.json")
    return d


def _move_room(path: Path, mat: np.ndarray) -> None:
    room = json.loads(path.read_text())
    for key in ("walls", "doors", "windows", "openings", "floors", "objects"):
        for e in room[key]:
            e["transform"] = list((mat @ np.asarray(e["transform"], float).reshape(4, 4).T).T.ravel())
    for s in room["sections"]:
        s["center"] = list(mat[:3, :3] @ np.asarray(s["center"]) + mat[:3, 3])
    path.write_text(json.dumps(room))


def test_myhome_exits_skip_closets(tmp_path: Path) -> None:
    """Closet doors are not exits once the house has other doors out; every room still reaches one."""
    scene = convert(MYHOME, tmp_path / "a")
    assert len(scene["exits"]) == 10  # was 21: one per door to any unscanned space
    assert any(w["code"] == "EXIT_POCKET_SKIPPED" for w in scene["warnings"])
    strict = convert(MYHOME, tmp_path / "b", unscanned_exits=False)
    assert _errors(strict) == [] and 0 < len(strict["exits"]) < len(scene["exits"])
    assert any(w["code"] == "UNSCANNED_DOOR_NOT_EXIT" for w in strict["warnings"])


def test_separate_session_room_is_realigned(tmp_path: Path) -> None:
    """A room from another ARSession (random yaw + offset + floor height) is moved back next to the others.

    Measured: 8.5 cm / 0.26 deg for Bedroom4 (2 shared doorways). The two copies of one doorway
    disagree by 5-19 cm in Apple's own single-session data, so 5 cm is out of reach from doorways.
    """
    d = _rooms_dir(tmp_path, MYHOME_ROOMS)
    k = MYHOME_ROOMS.index("Bedroom4")
    mat = rigid(np.radians(-169.5), np.array([2.0, -3.0]))
    mat[1, 3] = 0.2
    _move_room(d / f"room_{k}.json", mat)
    scene = convert(d, tmp_path / "out")
    assert _errors(scene) == []
    move = scene["extensions"]["roomplan"]["realigned_rooms"][str(k)]
    assert abs((move["yaw_deg"] - 169.5 + 180) % 360 - 180) < 1.0
    orig = _floor_polygon(json.loads((MYHOME / "Bedroom4" / "capturedRoom.json").read_text())["floors"][0])
    assert orig.centroid.distance(Polygon(scene["rooms"][k]["polygon"]).centroid) < 0.12


def test_single_session_folder_is_not_moved(tmp_path: Path) -> None:
    scene = convert(_rooms_dir(tmp_path, MYHOME_ROOMS), tmp_path / "out")
    assert scene["extensions"]["roomplan"]["realigned_rooms"] == {}
    assert not any("ALIGN" in w["code"] for w in scene["warnings"])


def test_room_files_without_structure(tmp_path: Path) -> None:
    """Our app writes room_N.json next to structure.json; an empty/broken structure falls back to them."""
    d = _rooms_dir(tmp_path, ["Bedroom2", "Hallway1", "Bedroom1"])
    (d / "structure.json").write_text("{}")
    (d / "scan_notes.json").write_text(json.dumps({"rooms": 3}))
    scene = convert(d, tmp_path / "out")
    assert len(scene["rooms"]) == 3 and _errors(scene) == []
    assert scene["extensions"]["roomplan"]["source_files"] == ["room_0.json", "room_1.json", "room_2.json"]
    assert any(w["code"] == "STRUCTURE_UNUSABLE" for w in scene["warnings"])
    assert any("outside" not in dr["connects"] for dr in scene["doors"])  # bedrooms join the hallway


def test_missing_floor_uses_wall_loop(tmp_path: Path) -> None:
    data = json.loads((MYHOME / "Bedroom4" / "capturedRoom.json").read_text())
    floor = _floor_polygon(data["floors"][0])
    (tmp_path / "room.json").write_text(json.dumps({**data, "floors": []}))
    scene = convert(tmp_path / "room.json", tmp_path / "out")
    assert _errors(scene) == [] and len(scene["rooms"]) == 1
    assert Polygon(scene["rooms"][0]["polygon"]).symmetric_difference(floor).area < 0.1 * floor.area
    assert any(w["code"] == "FLOOR_FROM_WALLS" for w in scene["warnings"])


def test_floor_drift_and_degenerate_surfaces(tmp_path: Path) -> None:
    """One room 8 cm higher (tracking drift) keeps its objects and openings; junk surfaces are skipped."""
    d = _rooms_dir(tmp_path, ["Diningroom", "Livingroom"])
    base = convert(d, tmp_path / "base")
    up = np.eye(4)
    up[1, 3] = 0.08
    _move_room(d / "room_1.json", up)
    room = json.loads((d / "room_1.json").read_text())
    room["walls"].append({**room["walls"][0], "dimensions": [0.0, 2.4, 0]})
    room["doors"].append({**room["doors"][0], "transform": [float("nan")] * 16})
    (d / "room_1.json").write_text(json.dumps(room))
    scene = convert(d, tmp_path / "out")
    assert _errors(scene) == []
    assert len(scene["doors"]) == len(base["doors"]) and len(scene["obstacles"]) == len(base["obstacles"])
    assert any(w["code"] == "DEGENERATE_SURFACE" for w in scene["warnings"])


def test_curved_wall(tmp_path: Path) -> None:
    data = json.loads((MYHOME / "Bedroom4" / "capturedRoom.json").read_text())
    i = max(range(len(data["walls"])), key=lambda j: data["walls"][j]["dimensions"][0])
    half, k = data["walls"][i]["dimensions"][0] / 2, 1.0
    rad = {"symbol": "rad", "converter": {"coefficient": 57.29577951308232, "constant": 0}}
    data["walls"][i]["curve"] = {"center": [0.0, -k], "radius": float(np.hypot(half, k)),
                                 "startAngle": {"value": float(np.arctan2(k, half)), "unit": rad},
                                 "endAngle": {"value": float(np.arctan2(k, -half)), "unit": rad}}
    (tmp_path / "room.json").write_text(json.dumps(data))
    straight = convert(MYHOME / "Bedroom4" / "capturedRoom.json", tmp_path / "a")
    scene = convert(tmp_path / "room.json", tmp_path / "b")
    assert _errors(scene) == []
    assert Polygon(scene["walls"][i]["polygon"]).area > Polygon(straight["walls"][i]["polygon"]).area + 0.01


def test_second_story(tmp_path: Path) -> None:
    d = _rooms_dir(tmp_path, ["Bedroom4", "Hallway2"])
    room = json.loads((d / "room_1.json").read_text())
    for key in ("walls", "doors", "windows", "openings", "floors", "objects", "sections"):
        for e in room[key]:
            e["story"] = 1
    (d / "room_1.json").write_text(json.dumps(room))
    up = np.eye(4)
    up[1, 3] = 3.0
    _move_room(d / "room_1.json", up)
    scene = convert(d, tmp_path / "out")
    assert [lv["elevation_m"] for lv in scene["levels"]] == pytest.approx([0.0, 3.0], abs=0.1)
    assert {r["level"] for r in scene["rooms"]} == {"L1", "L2"} and _errors(scene) == []
