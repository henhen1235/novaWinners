from pathlib import Path

import pytest

from ingest_blueprint.pipeline import run
from tools.make_synthetic_plan import build_spec, write_plan
from tools.score_against_truth import score_scenes


@pytest.mark.slow
def test_fifty_vector_plans(tmp_path: Path):
    iou = []
    door_recall = []
    door_precision = []
    exit_recall = []
    room_ok = 0
    connected = 0
    for seed in range(1, 51):
        directory = tmp_path / f"{seed:03d}"
        truth = write_plan(seed, directory)
        scene, code = run(directory / "plan.pdf", directory / "out", no_llm=True)
        score = score_scenes(scene, truth)
        limit = 0.12 if build_spec(seed)["scale_mode"] == "door" else 0.03
        assert code == 0
        assert score["scale_error"] <= limit
        iou.append(score["walkable_iou"])
        door_recall.append(score["door_recall"])
        door_precision.append(score["door_precision"])
        exit_recall.append(score["exit_recall"])
        room_ok += abs(score["room_delta"]) <= 1
        connected += int(score["connected"])
    assert min(iou) >= 0.92
    assert min(door_recall) >= 0.90
    assert min(door_precision) >= 0.85
    assert min(exit_recall) >= 0.95
    assert room_ok / 50 >= 0.90
    assert connected == 50
