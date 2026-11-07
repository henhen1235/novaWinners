import json
from pathlib import Path

import fitz
import trimesh

from ingest_blueprint.pipeline import run
from nav.pathfind import path_to_exit
from tools.make_synthetic_plan import build_spec, write_plan
from tools.score_against_truth import score_scenes

FAST_SEEDS = (1, 2, 3, 4, 7)


def _room(scene: dict, name: str) -> dict:
    return next(room for room in scene["rooms"] if room["name"] == name)


def test_seed_one_is_not_mirrored(tmp_path: Path):
    truth = write_plan(1, tmp_path)
    scene, code = run(tmp_path / "plan.pdf", tmp_path / "out", no_llm=True)
    assert code == 0
    annex = _room(scene, "West Annex")
    corridor = _room(scene, "Corridor")
    truth_annex = _room(truth, "West Annex")
    truth_corridor = _room(truth, "Corridor")
    assert annex["centroid"][0] < corridor["centroid"][0]
    predicted_offset = corridor["centroid"][0] - annex["centroid"][0]
    truth_offset = truth_corridor["centroid"][0] - truth_annex["centroid"][0]
    assert abs(predicted_offset - truth_offset) < 0.25


def test_vector_scores_on_fast_seeds(tmp_path: Path):
    for seed in FAST_SEEDS:
        directory = tmp_path / str(seed)
        truth = write_plan(seed, directory)
        scene, code = run(directory / "plan.pdf", directory / "out", no_llm=True)
        score = score_scenes(scene, truth)
        limit = 0.12 if build_spec(seed)["scale_mode"] == "door" else 0.03
        assert code == 0
        assert score["walkable_iou"] >= 0.92
        assert score["door_recall"] >= 0.90
        assert score["door_precision"] >= 0.85
        assert abs(score["room_delta"]) <= 1
        assert score["exit_recall"] >= 0.95
        assert score["scale_error"] <= limit
        assert score["connected"]


def test_display_glb_has_named_nodes(tmp_path: Path):
    write_plan(1, tmp_path)
    _scene, code = run(tmp_path / "plan.pdf", tmp_path / "out", no_llm=True)
    assert code == 0
    loaded = trimesh.load(tmp_path / "out" / "display.glb")
    names = set(loaded.graph.nodes)
    for name in ("walls", "floors", "doors", "exits"):
        assert name in names


def test_stair_on_one_level_is_an_exit(tmp_path: Path):
    write_plan(4, tmp_path)
    scene, code = run(tmp_path / "plan.pdf", tmp_path / "out", no_llm=True)
    assert code == 0
    assert any(exit_["leads_to"] == "stairwell" for exit_ in scene["exits"])


def test_two_pages_link_stairs(tmp_path: Path):
    write_plan(4, tmp_path)
    document = fitz.open()
    source = fitz.open(tmp_path / "plan.pdf")
    document.insert_pdf(source)
    document.insert_pdf(source)
    source.close()
    combined = tmp_path / "two.pdf"
    document.save(combined)
    document.close()
    scene, code = run(combined, tmp_path / "out", no_llm=True)
    assert code == 0
    assert [level["elevation_m"] for level in scene["levels"]] == [0.0, 3.0]
    assert scene["links"]
    assert all(link["cost_multiplier"] == 2.0 and link["type"] == "stairs" for link in scene["links"])


def test_path_probe_distance_is_finite(tmp_path: Path):
    write_plan(1, tmp_path)
    scene, code = run(tmp_path / "plan.pdf", tmp_path / "out", no_llm=True)
    assert code == 0
    room = scene["rooms"][0]
    result = path_to_exit(
        scene["navmesh"],
        (room["centroid"][0], room["centroid"][1]),
        scene["exits"],
        room["level"],
    )
    assert result["distance_m"] is not None
    assert result["distance_m"] > 0


def test_removing_then_restoring_a_door(tmp_path: Path):
    write_plan(2, tmp_path)
    out = tmp_path / "out"
    scene, code = run(tmp_path / "plan.pdf", out, no_llm=True)
    assert code == 0
    door = next(item for item in scene["doors"] if "outside" not in item["connects"])
    midpoint = door["segment"][0]
    other = door["segment"][1]
    center = ((midpoint[0] + other[0]) / 2.0, (midpoint[1] + other[1]) / 2.0)
    # Synthetic plans are drawn at 100 px/m. The scene is already in that pixel frame.
    pixel = [round(center[0] * 100), round(center[1] * 100)]
    blocked = {"operations": [{"op": "remove_door_at", "point": pixel}]}
    blocked_path = tmp_path / "blocked.json"
    blocked_path.write_text(json.dumps(blocked), encoding="utf-8")
    broken, broken_code = run(tmp_path / "plan.pdf", tmp_path / "blocked", no_llm=True, corrections_path=blocked_path)
    assert broken_code == 1
    assert any(item["code"] == "ROOM_UNREACHABLE" for item in broken["warnings"])
    restored = {"operations": [{"op": "add_door", "point": pixel, "width": 0.9}]}
    restored_path = tmp_path / "restored.json"
    restored_path.write_text(json.dumps(restored), encoding="utf-8")
    fixed, fixed_code = run(tmp_path / "plan.pdf", tmp_path / "fixed", no_llm=True, corrections_path=restored_path)
    assert fixed_code == 0
    room = fixed["rooms"][0]
    result = path_to_exit(
        fixed["navmesh"],
        (room["centroid"][0], room["centroid"][1]),
        fixed["exits"],
        room["level"],
    )
    assert result["distance_m"] is not None and result["distance_m"] < 1e6
