"""ingest_lidar.geometry: frame handedness, erosion, triangulation areas."""

from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

from ingest_lidar.geometry import (
    door_gap_box,
    exit_reachable_parts,
    extrude_xz,
    navmesh_from_walkable,
    ring,
    walkable_from_parts,
    walkable_to_contract,
)

# Asymmetric L: mirroring z -> -z (or swapping axes) would not map it onto itself.
L_SHAPE = Polygon([(1, 2), (5, 2), (5, 3), (2, 3), (2, 6), (1, 6)])


def _xz_footprint(mesh) -> Polygon:
    tris = [Polygon(f[:, [0, 2]]) for f in mesh.triangles]
    return unary_union([t for t in tris if t.area > 1e-9])


@pytest.mark.parametrize("poly", [L_SHAPE, L_SHAPE.reverse()], ids=["ccw", "cw"])
def test_extrude_xz_keeps_footprint_unmirrored(poly: Polygon) -> None:
    mesh = extrude_xz(poly, y0=3.5, height=2.5)
    assert _xz_footprint(mesh).symmetric_difference(L_SHAPE).area < 1e-9
    assert mesh.is_watertight and mesh.is_winding_consistent
    assert mesh.volume == pytest.approx(L_SHAPE.area * 2.5)  # positive: normals point outward
    assert mesh.bounds[:, 1].tolist() == pytest.approx([3.5, 6.0])


def _navmesh_area(nav: dict) -> float:
    v, t = np.asarray(nav["vertices"]), np.asarray(nav["triangles"])
    a, b, c = (v[t[:, k]][:, [0, 2]] for k in range(3))
    u, w = b - a, c - a
    return float(np.abs(u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0]).sum() / 2)


def test_navmesh_area_equals_walkable_area() -> None:
    free = [box(0, 0, 10, 5), box(10, 2, 14, 3), box(0, 5, 3, 9)]
    walkable = walkable_from_parts(free, [box(3, 1, 4, 2), box(6, 3, 7.5, 3.6)])
    nav = navmesh_from_walkable({"L1": walkable}, {"L1": 0.0})
    assert _navmesh_area(nav) == pytest.approx(walkable.area, rel=1e-3)
    assert len(nav["triangle_level"]) == len(nav["triangles"])
    assert {v[1] for v in nav["vertices"]} == {0.0}


def test_navmesh_triangles_face_up_and_use_level_elevation() -> None:
    nav = navmesh_from_walkable({"L2": walkable_from_parts([box(0, 0, 4, 4)], [])}, {"L2": 3.5})
    v, t = np.asarray(nav["vertices"]), np.asarray(nav["triangles"])
    normals = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    assert (normals[:, 1] > 0).all()
    assert set(v[:, 1]) == {3.5}


def test_walkable_from_parts_erodes_and_drops_slivers() -> None:
    # 0.4 m wide strip vanishes under a 0.25 m erosion; a 0.6 m x 0.6 m pad keeps 0.01 m^2 (< 0.05, dropped).
    walkable = walkable_from_parts([box(0, 0, 4, 4), box(10, 0, 10.4, 5), box(20, 0, 20.6, 0.6)], [])
    assert isinstance(walkable, MultiPolygon) and walkable.is_valid
    assert len(walkable.geoms) == 1
    assert walkable.area == pytest.approx(3.5 * 3.5)


def test_walkable_from_parts_empty_is_valid_multipolygon() -> None:
    walkable = walkable_from_parts([box(0, 0, 0.3, 0.3)], [])
    assert isinstance(walkable, MultiPolygon) and walkable.is_valid and walkable.is_empty


def test_walkable_to_contract_rounds_and_keeps_holes() -> None:
    walkable = walkable_from_parts([box(0, 0, 6, 6)], [box(2.5, 2.5, 3.5, 3.5)])
    out = walkable_to_contract(walkable)["polygons"]
    assert len(out) == 1 and len(out[0]["holes"]) == 1
    ext = out[0]["exterior"]
    assert ext[0] != ext[-1]  # no closing point
    assert all(round(c, 3) == c for p in ext + out[0]["holes"][0] for c in p)


def test_ring_rounds_to_mm_without_closing_point() -> None:
    assert ring(Polygon([(0, 0), (1.00049, 0), (1, 1.2344)])) == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.234]]


def test_door_gap_box_spans_wall() -> None:
    assert door_gap_box([[1, 2], [2, 2]], 0.15).bounds == pytest.approx((1, 1.915, 2, 2.085))
    assert door_gap_box([[4, 0], [4, 1.2]], 0.2).bounds == pytest.approx((3.89, 0, 4.11, 1.2))


def test_exit_reachable_parts_keeps_parts_near_exits() -> None:
    walkable = MultiPolygon([box(0, 0, 2, 2), box(10, 0, 12, 2)])
    kept = exit_reachable_parts(walkable, [[-0.5, 1.0]], tol_m=1.0)
    assert len(kept.geoms) == 1 and kept.bounds == (0, 0, 2, 2)
