"""Path B end to end on synthetic scans: up axis, no mirroring, walkable IoU, determinism."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon

from ingest_lidar.load import load_scan
from ingest_lidar.orient import orient
from ingest_lidar.pipeline import run
from tools.layouts import build_scene, corridor_office, tiny_office
from tools.make_synthetic_scan import make_scan


def _scan(tmp: Path, spec: dict, seed: int) -> Path:
    make_scan(build_scene(spec), seed, out_dir=tmp)
    return tmp


def _truth_to_scene(scene: dict, d: Path) -> np.ndarray:
    t = np.array(json.loads((d / "truth_transform.json").read_text())["truth_to_mesh"])
    return np.array(scene["extensions"]["lidar"]["input_to_scene"]) @ t


def _walk(scene: dict) -> MultiPolygon:
    return MultiPolygon([Polygon(p["exterior"], p["holes"]) for p in scene["walkable"]["L1"]["polygons"]]).buffer(0)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_up_axis_recovered_under_random_rotation(tmp_path: Path, seed: int) -> None:
    d = _scan(tmp_path, corridor_office(seed), seed)
    t = np.array(json.loads((d / "truth_transform.json").read_text())["truth_to_mesh"])
    m = orient(load_scan(d / "scan.obj")).matrix @ t
    up = m[:3, :3] @ [0, 1, 0]
    assert np.degrees(np.arccos(np.clip(up[1], -1, 1))) < 2.0
    assert np.linalg.det(m[:3, :3]) > 0.999  # proper rotation, never a reflection


def test_tiny_office_end_to_end_not_mirrored(tmp_path: Path) -> None:
    d = _scan(tmp_path / "scan", tiny_office(), 0)
    scene = run(d / "scan.obj", tmp_path / "out")
    assert [i for i in scene["extensions"]["lidar"]["validation"] if i["severity"] == "error"] == []
    m = _truth_to_scene(scene, d)
    truth = json.loads((d / "truth.scene.json").read_text())
    g = affinity.affine_transform(_walk(truth), [m[0, 0], m[0, 2], m[2, 0], m[2, 2], m[0, 3], m[2, 3]])
    p = _walk(scene)
    iou = p.intersection(g).area / p.union(g).area
    mirrored = affinity.scale(g, xfact=-1, origin=g.centroid)
    assert iou >= 0.88
    assert iou > p.intersection(mirrored).area / p.union(mirrored).area + 0.05
    assert len(scene["exits"]) == 2


@pytest.mark.slow
def test_deterministic_output(tmp_path: Path) -> None:
    d = _scan(tmp_path / "scan", tiny_office(), 4)
    outs = []
    for k in range(2):
        run(d / "scan.obj", tmp_path / f"o{k}")
        s = json.loads((tmp_path / f"o{k}" / "scene.json").read_text())
        s["meta"]["generated_at"] = ""
        outs.append(json.dumps(s))
    assert outs[0] == outs[1]
