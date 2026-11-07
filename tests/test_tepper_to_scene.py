"""Pure-geometry checks for tools.tepper_to_scene (the CMU source sheets are not in the repo)."""

from shapely.geometry import Polygon, box

from tools.tepper_to_scene import _hole_free, infer_door, open_connections


def test_hole_free_splits_until_no_holes():
    ring_wall = box(0, 0, 10, 10).difference(box(2, 2, 4, 4)).difference(box(6, 6, 8, 8))
    parts = _hole_free(ring_wall)
    assert all(isinstance(p, Polygon) and not p.interiors for p in parts)
    assert abs(sum(p.area for p in parts) - ring_wall.area) < 1e-6


def test_open_connection_only_without_wall():
    spaces = {"a": box(0, 0, 4, 3), "b": box(4.02, 0, 8, 3), "c": box(0, 3.2, 4, 6)}  # c is behind a 0.2 m wall
    doors = open_connections(spaces, [])
    assert [sorted(d["connects"]) for d in doors] == [["a", "b"]]
    assert doors[0]["kind"] == "opening" and abs(doors[0]["width"] - 3.0) < 0.05


def test_inferred_door_prefers_corridor_and_sits_in_the_wall():
    pf = {"spaces": {"office": box(0, 0, 4, 3), "hall": box(0, 3.2, 10, 5), "lab": box(4.2, 0, 8, 3)},
          "info": {"office": {"type": "office"}, "hall": {"type": "corridor"}, "lab": {"type": "office"}}}
    d = infer_door("office", pf, {"hall", "lab"})
    assert d["connects"] == ["office", "hall"] and d["width"] == 0.9
    zs = [p[1] for p in d["segment"]]
    assert all(abs(z - 3.1) < 1e-6 for z in zs)
