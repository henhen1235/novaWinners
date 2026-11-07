"""walls / rooms / obstacles on a hand-rasterized two-room plan (no mesh, < 1 s).

Plan (meters, x right, z down): outer walls on the rectangle x 0.5-9.5, z 0.5-5.5,
an interior wall at x = 5.0 with a 0.9 m door (z 2.0-2.9, floor under it) and a
0.8 m hole (z 4.0-4.8, no floor, no points), and a 1 x 1 m, 0.75 m tall box.
Walls are 0.15 m (3 cells) thick.
"""

import numpy as np
from shapely.geometry import Polygon

from ingest_lidar.grid import Grid, build_masks
from ingest_lidar.obstacles import detect_obstacles
from ingest_lidar.rooms import connect_unreachable, segment_rooms
from ingest_lidar.walls import detect_walls, wall_lines

RES = 0.05


def _cells(x0: float, x1: float, z0: float, z1: float) -> tuple[slice, slice]:
    return slice(round(z0 / RES), round(z1 / RES)), slice(round(x0 / RES), round(x1 / RES))


def _plan() -> Grid:
    shape = (120, 200)
    floor, body, upper = (np.zeros(shape, np.int32) for _ in range(3))
    maxh = np.zeros(shape, np.float32)
    floor[_cells(0.5, 9.5, 0.5, 5.5)] = 3
    walls = [(0.425, 9.575, 0.425, 0.575), (0.425, 9.575, 5.425, 5.575),
             (0.425, 0.575, 0.425, 5.575), (9.425, 9.575, 0.425, 5.575),
             (4.925, 5.075, 0.5, 2.0), (4.925, 5.075, 2.9, 4.0), (4.925, 5.075, 4.8, 5.5)]
    for w in walls:
        s = _cells(*w)
        floor[s], body[s], upper[s], maxh[s] = 0, 6, 3, 2.55
    upper[_cells(4.925, 5.075, 2.0, 2.9)] = 3  # lintel over the door
    floor[_cells(4.925, 5.075, 4.0, 4.8)] = 0  # hole: nothing seen at all
    box = _cells(2.0, 3.0, 2.0, 3.0)
    floor[box], body[box], maxh[box] = 0, 4, 0.75
    return Grid(0.0, 0.0, RES, floor, body, upper, maxh)


def test_two_room_plan() -> None:
    g = _plan()
    m = build_masks(g)
    walls = detect_walls(g, m, 3.0)
    rooms, doors = segment_rooms(g, m, walls)
    obstacles = detect_obstacles(g, m, walls)

    lines = wall_lines(walls)
    truth = {"h": [0.5, 5.5], "v": [0.5, 5.0, 9.5]}
    for ln in lines:
        assert min(abs(ln["c"] - c) for c in truth[ln["axis"]]) < 0.1, ln
    for axis, cs in truth.items():
        for c in cs:
            assert any(ln["axis"] == axis and abs(ln["c"] - c) < 0.1 for ln in lines), (axis, c)
    # the interior line is covered everywhere except the door (hole bridged)
    interior = sum(ln["hi"] - ln["lo"] for ln in lines if ln["axis"] == "v" and abs(ln["c"] - 5.0) < 0.1)
    assert 4.0 < interior < 4.4, interior

    assert len(rooms) == 2, [r["area_m2"] for r in rooms]
    assert all(Polygon(r["polygon"]).is_valid and r["area_m2"] > 15 for r in rooms)
    assert len(doors) == 1, doors
    (a, b), z = doors[0]["segment"], np.mean([p[1] for p in doors[0]["segment"]])
    assert abs(a[0] - 5.0) < 0.1 and abs(b[0] - 5.0) < 0.1 and abs(z - 2.45) < 0.1
    assert abs(doors[0]["width_m"] - 0.9) < 0.1
    assert set(doors[0]["connects"]) == {r["id"] for r in rooms}

    assert len(obstacles) == 1, obstacles
    o = obstacles[0]
    assert abs(Polygon(o["polygon"]).area - 1.0) < 0.15 and o["kind"] == "furniture"
    assert abs(o["height_m"] - 0.75) < 0.01


def test_deterministic() -> None:
    g = _plan()
    m = build_masks(g)
    w1, w2 = detect_walls(g, m, 3.0), detect_walls(g, m, 3.0)
    assert w1 == w2
    assert segment_rooms(g, m, w1) == segment_rooms(g, m, w2)


def test_missed_door_split() -> None:
    """Two 3 x 3 m rooms joined by a 0.8 m opening in a 0.15 m wall split into 2 with a door.

    With deltas 0.30-0.55 the median seed count only splits openings narrower
    than ~0.85 m (half width below the 4th delta)."""
    from ingest_lidar.rooms import _split

    g = _plan()
    comp = np.zeros(g.shape, bool)
    comp[_cells(0.5, 3.5, 0.5, 3.5)] = True
    comp[_cells(3.65, 6.65, 0.5, 3.5)] = True
    comp[_cells(3.5, 3.65, 1.6, 2.4)] = True
    parts, doors = _split(g, comp)
    assert len(np.unique(parts[parts > 0])) == 2
    assert len(doors) == 1 and abs(doors[0][3] - 0.8) < 0.1, doors
    big = np.zeros(g.shape, bool)
    big[_cells(0.5, 5.5, 0.5, 4.5)] = True
    assert len(_split(g, big)[1]) == 0


def test_unreachable_room_gets_an_opening_on_its_walkable_crossing() -> None:
    # D28: room B has no door path to the exit, but the walkable area crosses the door strip
    g = _plan()
    m = build_masks(g)
    rooms, doors = segment_rooms(g, m, detect_walls(g, m, 3.0))
    exit_ = {**doors[0], "id": "d_001", "connects": [rooms[0]["id"], "outside"], "is_exit": True}
    added = connect_unreachable(g, m, rooms, [exit_], 2)
    assert [d["connects"] for d in added] == [[rooms[0]["id"], rooms[1]["id"]]]
    (a, b), z = added[0]["segment"], np.mean([p[1] for p in added[0]["segment"]])
    assert abs(a[0] - 5.0) < 0.1 and abs(b[0] - 5.0) < 0.1 and abs(z - 2.45) < 0.1
    assert connect_unreachable(g, m, rooms, [exit_] + added, 3) == []
