"""Traced walls and skeleton doors on non-Manhattan synthetic scans (D29).

Builds the stress scans with a round room + curved corridor, a semicircular wall and a
rotated wing (tools.stress_scans), runs Path B and scores the non-axis walls only: their
centerline error must stay < 0.10 m and door recall >= 0.85.
"""

import math
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import MultiLineString, Polygon

from ingest_lidar.pipeline import run
from tools.make_synthetic_scan import make_scan
from tools.score_against_truth import _centerline, _load, scene_to_truth_2d, score
from tools.stress_scans import curved_wall, rotated_wing, round_room


def nonaxis_wall_error(scene: dict, truth_dir: Path) -> float:
    """Median distance (m) from points every 0.1 m on truth wall centerlines more than 5 deg
    off the scene's X/Z axes to the nearest predicted wall centerline (scorer's definition)."""
    truth = _load(Path(truth_dir) / "truth.scene.json")
    t2m = np.asarray(_load(Path(truth_dir) / "truth_transform.json")["truth_to_mesh"], float)
    f, _, _ = scene_to_truth_2d(np.asarray(scene["extensions"]["lidar"]["input_to_scene"], float) @ t2m)
    ax = f(np.array([[1.0, 0.0]])) - f(np.array([[0.0, 0.0]]))
    axis_deg = math.degrees(math.atan2(ax[0, 1], ax[0, 0]))
    target = MultiLineString([_centerline(Polygon(f(np.asarray(w["polygon"])))) for w in scene["walls"]])
    pts = []
    for w in truth["walls"]:
        line = _centerline(Polygon(w["polygon"]))
        (x0, z0), (x1, z1) = line.coords
        a = (math.degrees(math.atan2(z1 - z0, x1 - x0)) - axis_deg) % 90
        if min(a, 90 - a) > 5:
            pts += [line.interpolate(d) for d in np.arange(0, line.length + 1e-9, 0.1)]
    return float(np.median(shapely.distance(np.array(pts, dtype=object), target)))


@pytest.mark.parametrize("factory", [round_room, curved_wall, rotated_wing], ids=lambda f: f.__name__)
def test_non_manhattan_walls_and_doors(factory, tmp_path: Path) -> None:
    d = tmp_path / "scan"
    make_scan(factory(), seed=7, out_dir=d)
    scene = run(d / "scan.obj", tmp_path / "out", no_llm=True, cv=False)
    s = score(scene, d)
    assert any(w["id"].startswith("w_t") for w in scene["walls"]), "expected traced walls"
    assert nonaxis_wall_error(scene, d) < 0.10
    assert s["door_recall"] >= 0.85, s
    assert not s["validator_error_codes"], s["validator_error_codes"]
