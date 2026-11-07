"""ingest_plans: pure geometry, an end-to-end run on synthetic ESIM-style PDFs, and checks on the
committed CMU scenes (the CMU source sheets themselves are never in the repo)."""

from shapely.geometry import Point, Polygon, box

from ingest_plans.esim import _hole_free, infer_door, open_connections


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


def test_tepper_exits_are_at_grade_on_the_outline():
    """No exit inside a stair core (x901-x903) or off the building outline; nothing below L1 or above L5."""
    import json
    from pathlib import Path

    scene = json.loads((Path(__file__).resolve().parents[1] / "samples/cmu/tepper/scene.json").read_text())
    levels = {lv["id"] for lv in scene["levels"]}
    assert levels == {"L1", "L2", "L3", "L4", "L5"}
    assert all(lk["from"]["level"] in levels and lk["to"]["level"] in levels for lk in scene["links"])
    outline = {lv: Polygon(r) for lv, r in scene["extensions"]["tepper"]["outline"].items()}
    cores = [Polygon(r["polygon"]) for r in scene["rooms"] if r["name"][1:4] in ("901", "902", "903")]
    for e in scene["exits"]:
        pt = Point(e["position"])
        assert e["level"] in ("L1", "L2"), e
        assert outline[e["level"]].exterior.distance(pt) < 1.0, e
        assert not any(c.buffer(-0.3).contains(pt) for c in cores), e


# --- synthetic ESIM sheets ------------------------------------------------------------------
# 1 PDF point = 0.1 m. Outline 20 x 10 m; two rooms, a stair room and a corridor per floor,
# 0.2 m walls between area faces, door symbols as quarter arcs, exterior door on floor 1 only.
M = 0.1


def _sheet(path, floor: str, exterior_door: bool) -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    oc = {name: doc.add_ocg(name) for name in ("A-AREA", "A-SPAC-EXTR", "ARCH|A-DOOR", "ARCH|A-FLOR-STRS",
                                                "A-SPAC-IDEN-RMNO")}
    page.draw_rect(fitz.Rect(20, 20, 220, 120), color=(0, 0, 0), width=0.5, oc=oc["A-SPAC-EXTR"])
    rooms = {f"{floor}01": (23, 23, 80, 68), f"{floor}02": (82, 23, 140, 68), f"{floor}901": (142, 23, 217, 68),
             f"{floor}100": (23, 70, 217, 117)}
    for num, (x0, y0, x1, y1) in rooms.items():
        page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0, 0, 0), width=0.5, oc=oc["A-AREA"])
        sf = round((x1 - x0) * (y1 - y0) * M * M / 0.092903)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        page.insert_text((cx - 4, cy), num, fontsize=3, oc=oc["A-SPAC-IDEN-RMNO"])
        page.insert_text((cx - 4, cy + 4), f"{sf}sf", fontsize=2, oc=oc["A-SPAC-IDEN-RMNO"])
    for y in range(28, 64, 4):  # 9 treads of 6 m in the stair room
        page.draw_line(fitz.Point(150, y), fitz.Point(210, y), color=(0, 0, 0), oc=oc["ARCH|A-FLOR-STRS"])
    doors = [(45, 68, 1), (105, 68, 1), (170, 68, 1)]  # hinge x, wall y, swing into room above
    for x, y, _ in doors:
        page.draw_line(fitz.Point(x, y), fitz.Point(x, y - 9), color=(0, 0, 0), oc=oc["ARCH|A-DOOR"])
        page.draw_curve(fitz.Point(x, y - 9), fitz.Point(x + 9, y - 9), fitz.Point(x + 9, y), color=(0, 0, 0),
                        oc=oc["ARCH|A-DOOR"])
    if exterior_door:  # west wall of the corridor, swinging in
        page.draw_line(fitz.Point(22, 90), fitz.Point(31, 90), color=(0, 0, 0), oc=oc["ARCH|A-DOOR"])
        page.draw_curve(fitz.Point(31, 90), fitz.Point(31, 99), fitz.Point(22, 99), color=(0, 0, 0),
                        oc=oc["ARCH|A-DOOR"])
    doc.save(path)


def test_cli_on_synthetic_sheets(tmp_path):
    import json

    from contract.validate_scene import validate
    from ingest_plans.__main__ import main

    raw = tmp_path / "raw"
    raw.mkdir()
    _sheet(raw / "TST-1-ESIM-Base.pdf", "1", exterior_door=True)
    _sheet(raw / "TST-2-ESIM-Base.pdf", "2", exterior_door=False)
    cfg = tmp_path / "tst.json"
    cfg.write_text(json.dumps({"extends": "cmu", "name": "tst", "ext_key": "tst", "floor_height_m": 3.0}))
    out = tmp_path / "out"
    assert main([str(raw), "-o", str(out), "--config", str(cfg)]) == 0
    scene = json.loads((out / "scene.json").read_text())
    assert not [i for i in validate(scene) if i["severity"] == "error"]
    assert [lv["id"] for lv in scene["levels"]] == ["L1", "L2"]
    assert all(abs(v - M) < 0.002 for v in scene["extensions"]["tst"]["scale"].values())
    assert {r["name"] for r in scene["rooms"]} >= {"101", "102", "1901", "201", "202", "2901"}
    assert [e["level"] for e in scene["exits"]] == ["L1"]
    assert len(scene["stairs"]) == 1 and len(scene["links"]) == 1
    assert (out / "display.glb").stat().st_size > 0 and (out / "plan.svg").exists() and (out / "report.md").exists()


def test_mudge_rooms_reach_an_exit():
    """Committed Mudge scene: stairs go down, except basements (below L1) climb to the ground floor."""
    import json
    from pathlib import Path

    import networkx as nx

    import pytest

    path = Path(__file__).resolve().parents[1] / "samples/cmu/mudge/scene.json"
    if not path.exists():
        pytest.skip("Mudge sample not generated yet")
    scene = json.loads(path.read_text())
    elev = {lv["id"]: lv["elevation_m"] for lv in scene["levels"]}
    g = nx.DiGraph()
    g.add_nodes_from(r["id"] for r in scene["rooms"])
    for d in scene["doors"]:
        a, b = d["connects"]
        g.add_edges_from([(a, b), (b, a)])
    for s in scene["stairs"]:
        ends = {}
        for lvl, xz in ((s["from_level"], s["bottom"]), (s["to_level"], s["top"])):
            ends[lvl] = min((r for r in scene["rooms"] if r["level"] == lvl),
                            key=lambda r: Polygon(r["polygon"]).distance(Point(xz)))["id"]
        g.add_edge(ends[s["to_level"]], ends[s["from_level"]])
        if elev[s["from_level"]] < 0:
            g.add_edge(ends[s["from_level"]], ends[s["to_level"]])
    out = {r for e in scene["exits"] for d in scene["doors"] if d["id"] == e["door_id"] for r in d["connects"]}
    can_leave = set().union(*(nx.ancestors(g, r) | {r} for r in out if r in g))
    assert not [r["id"] for r in scene["rooms"] if r["id"] not in can_leave]
    assert all(e["level"] == "L1" for e in scene["exits"])
