import json
from pathlib import Path

from ingest_blueprint.pipeline import run
from ingest_blueprint.semantics import label_image
from tools.make_synthetic_plan import write_plan
from tools.score_against_truth import score_scenes


def test_raster_rooms_stay_close_on_single_line_plans(tmp_path: Path):
    for seed in (1, 2, 4):
        directory = tmp_path / str(seed)
        truth = write_plan(seed, directory, raster=True, distort=True)
        scene, _code = run(directory / "plan.png", directory / "out", no_llm=True)
        score = score_scenes(scene, truth)
        assert abs(score["room_delta"]) <= 1
        assert score["walkable_iou"] >= 0.80
        assert score["scale_error"] <= 0.12


def test_no_llm_skips_the_network_and_cache_replays(tmp_path: Path, monkeypatch):
    import numpy as np

    calls = {"n": 0}

    def fake_call(model, key, image):
        calls["n"] += 1
        return {"rooms": [{"name": "Lobby", "type": "lobby", "nx": 0.5, "nz": 0.5}], "exits": [], "stairs": []}

    monkeypatch.setattr("ingest_blueprint.semantics._call_openrouter", fake_call)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    cache = tmp_path / "llm_cache"
    first = label_image(image, cache, no_llm=False)
    second = label_image(image, cache, no_llm=False)
    skipped = label_image(image, cache, no_llm=True)
    assert calls["n"] == 1
    assert first == second
    assert first[0]["name"] == "Lobby"
    assert skipped == []
    assert list(cache.glob("*.json"))
    stored = json.loads(next(cache.glob("*.json")).read_text(encoding="utf-8"))
    assert stored["rooms"][0]["name"] == "Lobby"
