"""Pure-geometry checks for tools.tepper_to_scene (the CMU source sheets are not in the repo)."""

from shapely.geometry import Point, Polygon, box

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


def test_every_tepper_room_reaches_an_exit_going_down():
    """Committed scene: doors work both ways, stairs only downward, and every room gets out."""
    import json
    from pathlib import Path

    import networkx as nx

    scene = json.loads((Path(__file__).resolve().parents[1] / "samples/cmu/tepper/scene.json").read_text())
    elev = {lv["id"]: lv["elevation_m"] for lv in scene["levels"]}
    g = nx.DiGraph()
    g.add_nodes_from(r["id"] for r in scene["rooms"])
    for d in scene["doors"]:
        a, b = d["connects"]
        g.add_edges_from([(a, b), (b, a)])
    stairs = {s["id"]: s for s in scene["stairs"]}
    assert len(stairs) == len(scene["links"])
    for s in stairs.values():
        lo, hi = s["from_level"], s["to_level"]
        assert elev[hi] > elev[lo]
        room_at = {}
        for lvl, xz in ((lo, s["bottom"]), (hi, s["top"])):
            room_at[lvl] = min((r for r in scene["rooms"] if r["level"] == lvl),
                               key=lambda r: Polygon(r["polygon"]).distance(Point(xz)))["id"]
        g.add_edge(room_at[hi], room_at[lo])
    out = {r for e in scene["exits"] for d in scene["doors"] if d["id"] == e["door_id"] for r in d["connects"]}
    can_leave = set().union(*(nx.ancestors(g, r) | {r} for r in out if r in g))
    stuck = [r["id"] for r in scene["rooms"] if r["id"] not in can_leave]
    assert not stuck, stuck
