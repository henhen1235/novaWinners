"""Regressions found on real plans (CMU Tepper CAD sheets) and curved geometry."""

from pathlib import Path

from shapely.geometry import box

from geometry.infer_doors import doors_from_arcs, infer_doors
from geometry.rooms import label_faces
from geometry.vectorize import split_gaps
from ingest_blueprint.pipeline import run
from ingest_blueprint.scale import area_label_ratio
from ingest_blueprint.types import Segment
from tools.make_curved_plan import build
from tools.score_against_truth import score_scenes
from tools.swiss_to_plan import render_plan


def test_split_gaps_survives_identical_segments():
    wall = Segment((0.0, 0.0), (4.0, 0.0), 0.2)
    walls, _doors = split_gaps([wall, Segment(wall.p1, wall.p2, 0.2)])
    assert walls


def test_area_tags_fix_scale():
    # Faces drawn at half size: 4 x 4 m rooms tagged 16 m2 (172 sf) read as 2 x 2 m.
    faces = [box(x, 0, x + 2, 2) for x in (0, 3, 6)]
    texts = [("172 SF", (x + 1, 1)) for x in (0, 3, 6)] + [("3401", (1, 1.5))]
    ratio = area_label_ratio(faces, texts, wall_m=0.0)
    assert ratio is not None and abs(ratio - 2.0) < 0.02


def test_room_number_names_the_room_not_its_area_tag():
    labels = label_faces([box(0, 0, 4, 4)], [("130sf", (2, 2.5), 2.0), ("3404", (2, 2), 2.0)])
    assert labels[0][0] == "3404"


def test_enclosed_rooms_get_inferred_doors_to_the_corridor():
    corridor = box(0, 0, 12, 2)
    offices = [box(x, 2, x + 4, 6) for x in (0, 4, 8)]
    doors = infer_doors([corridor] + offices, [])
    assert sum(door.is_exit for door in doors) == 1
    assert len(doors) == 4
    assert all(door.inferred for door in doors)


def test_curved_plan_rooms_and_exits(tmp_path: Path):
    walls, rooms, doors = build()
    render_plan(walls, rooms, doors, [], tmp_path, 100.0)
    scene, code = run(tmp_path / "plan.png", tmp_path / "out", no_llm=True)
    score = score_scenes(scene, __import__("json").loads((tmp_path / "truth.scene.json").read_text()))
    assert code == 0
    assert abs(score["room_delta"]) <= 1
    assert score["walkable_iou"] >= 0.75
    assert score["exit_recall"] == 1.0


def test_door_arcs_on_two_separate_buildings():
    # Two disjoint wings: the union of faces is a MultiPolygon (crashed on Tepper TSB-1).
    faces = [box(0, 0, 4, 4), box(4, 0, 8, 4), box(20, 0, 24, 4)]
    doors = doors_from_arcs(faces, [((4.0, 1.0), (3.6, 1.6), 0.9), ((24.0, 1.0), (23.5, 1.6), 0.9)])
    assert len(doors) == 2
    assert [door.is_exit for door in doors] == [False, True]


def test_open_to_below_faces_are_not_floor():
    from ingest_blueprint.pipeline import _drop_voids
    from ingest_blueprint.types import PlanPage, ScaleEstimate, TextSpan
    import numpy as np

    page = PlanPage("x", 0, 10, 10, np.zeros((1, 1, 3), np.uint8), [], [TextSpan("OTB3-4", (150, 150, 250, 250), 2.0)])
    notes: dict = {}
    kept = _drop_voids([box(0, 0, 1, 1), box(1.5, 1.5, 2.5, 2.5)], page, ScaleEstimate(100.0, "manual", 1.0), notes)
    assert len(kept) == 1 and notes["void_faces_dropped"] == 1
