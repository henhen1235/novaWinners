"""RoomScanner Mesh-mode PLY (ARKit classified mesh) loads with per-face classes."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ingest_lidar.load import ARKIT_PLY_COMMENT, load_scan

FACE_DT = np.dtype([("n", "u1"), ("idx", "<i4", (3,)), ("cls", "u1")])


def write_arkit_ply(path: Path, v: np.ndarray, f: np.ndarray, cls: np.ndarray) -> None:
    hdr = (b"ply\nformat binary_little_endian 1.0\n" + ARKIT_PLY_COMMENT + b"\n"
           + f"element vertex {len(v)}\nproperty float x\nproperty float y\nproperty float z\n"
             f"element face {len(f)}\nproperty list uchar int vertex_indices\n"
             "property uchar classification\nend_header\n".encode())
    faces = np.zeros(len(f), FACE_DT)
    faces["n"], faces["idx"], faces["cls"] = 3, f, cls
    path.write_bytes(hdr + v.astype("<f4").tobytes() + faces.tobytes())


def test_classes_and_geometry_round_trip(tmp_path: Path) -> None:
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1], [0, 2.5, 0]], float)
    f = np.array([[0, 2, 1], [0, 1, 3]])
    write_arkit_ply(tmp_path / "mesh.ply", v, f, np.array([2, 1], np.uint8))
    scan = load_scan(tmp_path / "mesh.ply")
    assert np.allclose(scan.vertices, v) and (scan.faces == f).all()
    assert scan.face_classes.tolist() == [2, 1]
    assert scan.info["arkit_classes"]["floor"] == 1 and scan.info["arkit_classes"]["wall"] == 1


def test_bad_index_is_rejected(tmp_path: Path) -> None:
    v = np.zeros((3, 3))
    write_arkit_ply(tmp_path / "bad.ply", v, np.array([[0, 1, 9]]), np.array([0], np.uint8))
    with pytest.raises(ValueError):
        load_scan(tmp_path / "bad.ply")
