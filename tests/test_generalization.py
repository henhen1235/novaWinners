"""Regression checks for the stress-set fixes (research/GENERALIZATION.md, D20-D24)."""

import numpy as np

from ingest_lidar.grid import upper_band
from ingest_lidar.pipeline import _drop_navmesh_islands


def test_upper_band_stays_below_low_ceiling() -> None:
    assert upper_band(3.0) == (2.0, 2.4)  # unchanged for the benchmark's 3 m ceilings
    lo, hi = upper_band(2.3)
    assert hi < 2.3 - 0.1 and lo < hi


def test_vertex_disjoint_navmesh_piece_far_from_exits_is_dropped() -> None:
    verts = [[0, 0, 0], [1, 0, 0], [0, 0, 1], [5, 0, 5], [6, 0, 5], [5, 0, 6]]
    nav = {"vertices": verts, "triangles": [[0, 1, 2], [3, 4, 5]], "triangle_level": ["L1", "L1"]}
    out, dropped = _drop_navmesh_islands(nav, [[0.0, 0.5]])
    assert out["triangles"] == [[0, 1, 2]] and np.isclose(dropped, 0.5)
