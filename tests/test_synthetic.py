"""tools.make_synthetic_scan and tools.score_against_truth: generator output and
scorer exactness, plus a mirror check on an asymmetric L layout."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from contract.validate_scene import validate
from tools import layouts
from tools.make_synthetic_scan import make_scan, random_pose
from tools.score_against_truth import perfect_prediction, score, transform_scene_xz


@pytest.fixture(scope="module")
def tiny_scan(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("tiny")
    make_scan(layouts.build_scene(layouts.tiny_office()), seed=0, out_dir=out)
    return out


def test_scan_files_and_triangle_count(tiny_scan: Path) -> None:
    mesh = trimesh.load(tiny_scan / "scan.ply", force="mesh")
    assert 60_000 < len(mesh.faces) < 200_000
    assert (tiny_scan / "scan.obj").stat().st_size > 0


def test_truth_passes_validator_and_has_furniture(tiny_scan: Path) -> None:
    truth = json.loads((tiny_scan / "truth.scene.json").read_text())
    assert [i for i in validate(truth) if i["severity"] == "error"] == []
    base = layouts.build_scene(layouts.tiny_office())
    assert len(truth["obstacles"]) > len(base["obstacles"])
    assert any(o["kind"] == "pillar" for o in truth["obstacles"])


def test_transform_is_proper_rotation(tiny_scan: Path) -> None:
    m = np.asarray(json.loads((tiny_scan / "truth_transform.json").read_text())["truth_to_mesh"])
    r = m[:3, :3]
    assert np.allclose(r @ r.T, np.eye(3), atol=1e-9)
    assert np.linalg.det(r) == pytest.approx(1.0)
    assert np.allclose(m[3], [0, 0, 0, 1])
    assert np.linalg.norm(m[:3, 3]) <= 10.0


def test_perfect_prediction_scores_exactly(tiny_scan: Path) -> None:
    s = score(perfect_prediction(tiny_scan, yaw_deg=30.0, shift=(3.0, -2.0)), tiny_scan)
    assert s["up_error_deg"] == pytest.approx(0.0, abs=1e-6)
    assert s["walkable_iou"] == pytest.approx(1.0, abs=1e-6)
    assert s["door_recall"] == 1.0 and s["door_precision"] == 1.0
    assert s["room_count_diff"] == 0
    assert s["wall_median_error_m"] == pytest.approx(0.0, abs=1e-6)
    assert s["obstacle_recall"] == 1.0
    assert s["validator_errors"] == 0
    assert not s["mirrored"]


def _l_layout() -> dict:
    """8 x 4 m room with a 4 x 6 m arm off its west half: an L, not mirror-symmetric."""
    lv = "L1"
    return {
        "name": "l_shape", "wall_thickness_m": 0.15, "agent_radius_m": 0.25,
        "levels": [{"id": lv, "name": "Ground", "elevation_m": 0.0, "ceiling_height_m": 3.0}],
        "rooms": [
            {"id": "a", "level": lv, "name": "A", "type": "office", "rect": [0, 0, 8, 4]},
            {"id": "b", "level": lv, "name": "B", "type": "office", "rect": [0, 4, 4, 10]},
        ],
        "doors": [
            {"level": lv, "center": [2, 4], "axis": "x", "width": 0.9},
            {"level": lv, "center": [8, 2], "axis": "z", "width": 1.2, "exit": {"label": "Exit"}},
        ],
    }


def test_mirrored_prediction_is_caught(tmp_path: Path) -> None:
    truth = layouts.build_scene(_l_layout())
    (tmp_path / "truth.scene.json").write_text(json.dumps(truth))
    pose = random_pose(np.random.default_rng(3))
    (tmp_path / "truth_transform.json").write_text(json.dumps({"truth_to_mesh": pose.tolist()}))
    good = perfect_prediction(tmp_path, yaw_deg=90.0, shift=(1.0, 1.0))
    assert score(good, tmp_path)["walkable_iou"] == pytest.approx(1.0, abs=1e-6)
    # Mirror x -> 8 - x in the scene frame but keep the recorded transform: a
    # pipeline that flipped handedness without saying so.
    mirrored = transform_scene_xz(perfect_prediction(tmp_path, yaw_deg=0.0, shift=(0.0, 0.0)),
                                  np.diag([-1.0, 1.0]), np.array([8.0, 0.0]))
    assert score(mirrored, tmp_path)["walkable_iou"] < 0.6
