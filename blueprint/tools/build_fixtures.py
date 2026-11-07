"""Write the hand-authored scene fixtures from explicit meter geometry.

Run: ``python tools/build_fixtures.py``
The polygons below are the fixture design. Navmesh triangles are derived
from those polygons so the area check can pass; they are not copied from a
pipeline run.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from shapely.geometry import Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT.parent))  # repo-root contract package

from contract.models import Scene  # noqa: E402
from contract.validate_scene import validate as validate_data  # noqa: E402
from geometry.polygons import geometry_to_rings, ring_coords, round_tree  # noqa: E402
from geometry.walkable import build_walkable  # noqa: E402
from nav.navmesh import build_navmesh  # noqa: E402

FIXTURES = ROOT / "fixtures"
NEGATIVE = FIXTURES / "negative"


def rect(x0: float, z0: float, x1: float, z1: float) -> Polygon:
    return Polygon([(x0, z0), (x1, z0), (x1, z1), (x0, z1)])


def door_seg(x0: float, z0: float, x1: float, z1: float) -> list[list[float]]:
    return [[x0, z0], [x1, z1]]


def room_record(room_id: str, level: str, name: str, room_type: str, poly: Polygon) -> dict:
    centroid = poly.centroid
    return {
        "id": room_id,
        "level": level,
        "name": name,
        "type": room_type,
        "polygon": ring_coords(poly.exterior),
        "area_m2": float(poly.area),
        "centroid": [float(centroid.x), float(centroid.y)],
        "has_window": False,
    }


def wall_record(wall_id: str, level: str, poly: Polygon, height: float = 2.7) -> dict:
    holes = [ring_coords(hole) for hole in poly.interiors]
    record = {
        "id": wall_id,
        "level": level,
        "polygon": ring_coords(poly.exterior),
        "height_m": height,
    }
    if holes:
        # Walls in the contract are a single ring. Stamp holes into extensions via a
        # solid frame approximated by the exterior only when there are no holes.
        # Callers pass hole-free pieces plus one framed shell stored as exterior.
        pass
    return record


def meta(name: str) -> dict:
    return {
        "name": name,
        "source": "synthetic",
        "source_file": "fixtures",
        "units": "meters",
        "up_axis": "y",
        "scale_method": "manual",
        "scale_confidence": 1.0,
        "generated_by": "tools/build_fixtures.py",
        "generated_at": "2026-01-01T00:00:00Z",
    }


def assemble(
    name: str,
    levels: list[dict],
    walls: list[dict],
    rooms: list[dict],
    doors: list[dict],
    exits: list[dict],
    walkable_geoms: dict[str, Polygon],
    elevations: dict[str, float],
    stairs: list[dict] | None = None,
    links: list[dict] | None = None,
    obstacles: list[dict] | None = None,
    pois: list[dict] | None = None,
) -> dict:
    walkable = {}
    nav_levels = []
    for level in levels:
        geom = walkable_geoms[level["id"]]
        walkable[level["id"]] = {"polygons": geometry_to_rings(geom)}
        nav_levels.append((level["id"], elevations[level["id"]], geom))
    scene = {
        "schema_version": "1.0",
        "meta": meta(name),
        "levels": levels,
        "walls": walls,
        "rooms": rooms,
        "doors": doors,
        "exits": exits,
        "stairs": stairs or [],
        "elevators": [],
        "obstacles": obstacles or [],
        "points_of_interest": pois or [],
        "walkable": walkable,
        "navmesh": build_navmesh(nav_levels, agent_radius_m=0.25),
        "links": links or [],
        "assets": {
            "display_model": "display.glb",
            "floorplan_overlay": "overlay.png",
            "navmesh_debug": "navmesh_debug.glb",
        },
        "warnings": [],
        "extensions": {},
    }
    return round_tree(scene)


def door(
    door_id: str,
    level: str,
    segment: list[list[float]],
    connects: list[str],
    is_exit: bool,
    opens_toward: str,
) -> dict:
    x0, z0 = segment[0]
    x1, z1 = segment[1]
    width = ((x1 - x0) ** 2 + (z1 - z0) ** 2) ** 0.5
    return {
        "id": door_id,
        "level": level,
        "segment": segment,
        "width_m": width,
        "connects": connects,
        "is_exit": is_exit,
        "kind": "swing",
        "fire_rated": None,
        "opens_toward": opens_toward,
    }


def exit_record(exit_id: str, level: str, door_id: str, segment: list[list[float]], label: str, assembly: list[float]) -> dict:
    position = [(segment[0][0] + segment[1][0]) / 2, (segment[0][1] + segment[1][1]) / 2]
    width = ((segment[1][0] - segment[0][0]) ** 2 + (segment[1][1] - segment[0][1]) ** 2) ** 0.5
    return {
        "id": exit_id,
        "level": level,
        "door_id": door_id,
        "position": position,
        "width_m": width,
        "leads_to": "outside",
        "label": label,
        "assembly_point": assembly,
    }


def tiny_parts() -> tuple[dict, dict[str, Polygon]]:
    """Six offices, one corridor, two exits. Interiors are meters, XZ plane."""

    offices = {
        "r_001": ("Office 1", rect(0.2, 0.2, 3.8, 3.8)),
        "r_002": ("Office 2", rect(4.2, 0.2, 7.8, 3.8)),
        "r_003": ("Office 3", rect(8.2, 0.2, 11.8, 3.8)),
        "r_004": ("Office 4", rect(0.2, 6.2, 3.8, 9.8)),
        "r_005": ("Office 5", rect(4.2, 6.2, 7.8, 9.8)),
        "r_006": ("Office 6", rect(8.2, 6.2, 11.8, 9.8)),
    }
    corridor = rect(0.2, 4.0, 11.8, 6.0)
    room_polys = {key: poly for key, (_, poly) in offices.items()}
    room_polys["r_007"] = corridor
    rooms = [room_record(key, "L1", name, "office", poly) for key, (name, poly) in offices.items()]
    rooms.append(room_record("r_007", "L1", "Corridor", "corridor", corridor))

    gaps = [
        door_seg(1.55, 3.9, 2.45, 3.9),
        door_seg(5.55, 3.9, 6.45, 3.9),
        door_seg(9.55, 3.9, 10.45, 3.9),
        door_seg(1.55, 6.1, 2.45, 6.1),
        door_seg(5.55, 6.1, 6.45, 6.1),
        door_seg(9.55, 6.1, 10.45, 6.1),
    ]
    connects = [
        ("r_001", "r_007", "r_001"),
        ("r_002", "r_007", "r_002"),
        ("r_003", "r_007", "r_003"),
        ("r_004", "r_007", "r_004"),
        ("r_005", "r_007", "r_005"),
        ("r_006", "r_007", "r_006"),
    ]
    doors = []
    for index, (segment, (left, right, toward)) in enumerate(zip(gaps, connects), start=1):
        doors.append(door(f"d_{index:03d}", "L1", segment, [left, right], False, toward))
    west = door_seg(0.1, 4.55, 0.1, 5.45)
    east = door_seg(11.9, 4.55, 11.9, 5.45)
    doors.append(door("d_007", "L1", west, ["r_007", "outside"], True, "outside"))
    doors.append(door("d_008", "L1", east, ["r_007", "outside"], True, "outside"))
    exits = [
        exit_record("e_001", "L1", "d_007", west, "West Exit", [-1.5, 5.0]),
        exit_record("e_002", "L1", "d_008", east, "East Exit", [13.5, 5.0]),
    ]
    frame = Polygon(
        [(0.0, 0.0), (12.0, 0.0), (12.0, 10.0), (0.0, 10.0)],
        [[(0.2, 0.2), (0.2, 9.8), (11.8, 9.8), (11.8, 0.2)]],
    )
    # Contract walls are one ring. Use the frame exterior as a solid slab only if
    # it would cover rooms. Instead store the outer band as four rectangles.
    wall_polys = [
        rect(0.0, 0.0, 12.0, 0.2),
        rect(0.0, 9.8, 12.0, 10.0),
        rect(0.0, 0.2, 0.2, 9.8),
        rect(11.8, 0.2, 12.0, 9.8),
        rect(3.8, 0.2, 4.2, 3.8),
        rect(7.8, 0.2, 8.2, 3.8),
        rect(3.8, 6.2, 4.2, 9.8),
        rect(7.8, 6.2, 8.2, 9.8),
        rect(0.2, 3.8, 11.8, 4.0),
        rect(0.2, 6.0, 11.8, 6.2),
    ]
    walls = [wall_record(f"w_{index:03d}", "L1", poly) for index, poly in enumerate(wall_polys, start=1)]
    walk = build_walkable(
        list(room_polys.values()),
        wall_polys,
        [],
        gaps + [west, east],
        wall_thickness_m=0.2,
        agent_radius_m=0.25,
    )
    scene = assemble(
        "Tiny Office",
        [{"id": "L1", "name": "Ground floor", "elevation_m": 0.0, "ceiling_height_m": 2.7}],
        walls,
        rooms,
        doors,
        exits,
        {"L1": walk},
        {"L1": 0.0},
        pois=[
            {"id": "p_001", "level": "L1", "position": [1.2, 5.0], "kind": "fire_exit_sign"},
        ],
    )
    return scene, {"L1": walk, "rooms": room_polys, "walls": wall_polys}


def school_scene() -> dict:
    """Two classrooms, lobby, corridor, and two stairs on each of two levels."""

    def level_rooms(prefix_start: int, level: str) -> tuple[list[dict], dict[str, Polygon], list]:
        specs = [
            (f"r_{prefix_start:03d}", "Classroom A", "classroom", rect(0.2, 0.2, 7.8, 3.8)),
            (f"r_{prefix_start + 1:03d}", "Classroom B", "classroom", rect(8.2, 0.2, 15.8, 3.8)),
            (f"r_{prefix_start + 2:03d}", "Corridor", "corridor", rect(0.2, 4.0, 15.8, 6.0)),
            (f"r_{prefix_start + 3:03d}", "Stair West", "stairwell", rect(0.2, 6.2, 4.0, 10.0)),
            (f"r_{prefix_start + 4:03d}", "Stair East", "stairwell", rect(12.0, 6.2, 15.8, 10.0)),
            (f"r_{prefix_start + 5:03d}", "Lobby", "lobby", rect(4.2, 6.2, 11.8, 10.0)),
        ]
        rooms = [room_record(rid, level, name, kind, poly) for rid, name, kind, poly in specs]
        polys = {rid: poly for rid, _, _, poly in specs}
        corridor_id = specs[2][0]
        stair_w = specs[3][0]
        stair_e = specs[4][0]
        lobby_id = specs[5][0]
        class_a = specs[0][0]
        class_b = specs[1][0]
        segments = [
            (door_seg(3.55, 3.9, 4.45, 3.9), [class_a, corridor_id], class_a, False),
            (door_seg(11.55, 3.9, 12.45, 3.9), [class_b, corridor_id], class_b, False),
            (door_seg(1.65, 6.1, 2.55, 6.1), [stair_w, corridor_id], stair_w, False),
            (door_seg(13.45, 6.1, 14.35, 6.1), [stair_e, corridor_id], stair_e, False),
            (door_seg(7.55, 6.1, 8.45, 6.1), [lobby_id, corridor_id], lobby_id, False),
        ]
        return rooms, polys, segments

    rooms1, polys1, segs1 = level_rooms(1, "L1")
    rooms2, polys2, segs2 = level_rooms(7, "L2")
    doors = []
    next_door = 1
    for level, segments in (("L1", segs1), ("L2", segs2)):
        for segment, connects, toward, is_exit in segments:
            doors.append(door(f"d_{next_door:03d}", level, segment, connects, is_exit, toward))
            next_door += 1
    east = door_seg(15.9, 4.55, 15.9, 5.45)
    doors.append(door("d_011", "L1", east, ["r_003", "outside"], True, "outside"))
    exits = [exit_record("e_001", "L1", "d_011", east, "Main Entrance", [17.5, 5.0])]

    def walls_for(level: str, start: int) -> tuple[list[dict], list[Polygon]]:
        polys = [
            rect(0.0, 0.0, 16.0, 0.2),
            rect(0.0, 9.8, 16.0, 10.0),
            rect(0.0, 0.2, 0.2, 9.8),
            rect(15.8, 0.2, 16.0, 9.8),
            rect(7.8, 0.2, 8.2, 3.8),
            rect(0.2, 3.8, 15.8, 4.0),
            rect(0.2, 6.0, 15.8, 6.2),
            rect(4.0, 6.2, 4.2, 10.0),
            rect(11.8, 6.2, 12.0, 10.0),
        ]
        records = [wall_record(f"w_{start + index:03d}", level, poly) for index, poly in enumerate(polys)]
        return records, polys

    walls1, wall_polys1 = walls_for("L1", 1)
    walls2, wall_polys2 = walls_for("L2", 10)
    walk1 = build_walkable(list(polys1.values()), wall_polys1, [], [item[0] for item in segs1] + [east], 0.2, 0.25)
    walk2 = build_walkable(list(polys2.values()), wall_polys2, [], [item[0] for item in segs2], 0.2, 0.25)
    stair_w = rect(0.2, 6.2, 4.0, 10.0)
    stair_e = rect(12.0, 6.2, 15.8, 10.0)
    bottom_w = [2.1, 8.1]
    bottom_e = [13.9, 8.1]
    stairs = [
        {
            "id": "s_001",
            "from_level": "L1",
            "to_level": "L2",
            "polygon": ring_coords(stair_w.exterior),
            "bottom": bottom_w,
            "top": bottom_w,
            "width_m": 1.2,
            "direction": "both",
            "is_enclosed": True,
        },
        {
            "id": "s_002",
            "from_level": "L1",
            "to_level": "L2",
            "polygon": ring_coords(stair_e.exterior),
            "bottom": bottom_e,
            "top": bottom_e,
            "width_m": 1.2,
            "direction": "both",
            "is_enclosed": True,
        },
    ]
    links = [
        {
            "id": "link_001",
            "type": "stairs",
            "from": {"level": "L1", "position": bottom_w},
            "to": {"level": "L2", "position": bottom_w},
            "cost_multiplier": 2.0,
            "bidirectional": True,
        },
        {
            "id": "link_002",
            "type": "stairs",
            "from": {"level": "L1", "position": bottom_e},
            "to": {"level": "L2", "position": bottom_e},
            "cost_multiplier": 2.0,
            "bidirectional": True,
        },
    ]
    return assemble(
        "Two Floor School",
        [
            {"id": "L1", "name": "Ground floor", "elevation_m": 0.0, "ceiling_height_m": 2.7},
            {"id": "L2", "name": "First floor", "elevation_m": 3.0, "ceiling_height_m": 2.7},
        ],
        walls1 + walls2,
        rooms1 + rooms2,
        doors,
        exits,
        {"L1": walk1, "L2": walk2},
        {"L1": 0.0, "L2": 3.0},
        stairs=stairs,
        links=links,
    )


def write_scene(path: Path, scene: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Scene.model_validate(scene)
    path.write_text(json.dumps(scene, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    issues = validate_data(json.loads(path.read_text(encoding="utf-8")))
    if issues:
        raise SystemExit(f"{path} failed validation:\n" + json.dumps(issues, indent=2))


def negative_variants(tiny: dict, walk: Polygon) -> dict[str, dict]:
    variants: dict[str, dict] = {}

    no_exits = copy.deepcopy(tiny)
    no_exits["exits"] = []
    for item in no_exits["doors"]:
        item["is_exit"] = False
    variants["no_exits"] = no_exits

    unreachable = copy.deepcopy(tiny)
    unreachable["doors"] = [item for item in unreachable["doors"] if item["id"] != "d_001"]
    variants["room_unreachable"] = unreachable

    exit_bad = copy.deepcopy(tiny)
    exit_bad["doors"].append(
        door("d_099", "L1", door_seg(0.1, 1.0, 0.1, 1.9), ["outside", "outside"], True, "outside")
    )
    exit_bad["exits"].append(exit_record("e_099", "L1", "d_099", door_seg(0.1, 1.0, 0.1, 1.9), "Nowhere", [-1.0, 1.5]))
    variants["exit_unreachable"] = exit_bad

    narrow = copy.deepcopy(tiny)
    for item in narrow["doors"]:
        if item["id"] == "d_001":
            item["width_m"] = 0.4
    variants["door_too_narrow"] = narrow

    small = copy.deepcopy(tiny)
    closet = rect(5.5, 6.25, 6.3, 7.05)
    small["rooms"].append(room_record("r_099", "L1", "Closet", "storage", closet))
    small_door = door_seg(5.6, 6.12, 6.3, 6.12)
    small["doors"].append(door("d_098", "L1", small_door, ["r_099", "r_007"], False, "r_099"))
    variants["small_room_artifact"] = small

    scale = copy.deepcopy(tiny)
    for item in scale["doors"]:
        item["width_m"] = 1.6
    for item in scale["exits"]:
        item["width_m"] = 1.6
    variants["scale_low_confidence"] = scale

    unknown = copy.deepcopy(tiny)
    for item in unknown["doors"]:
        if item["id"] == "d_002":
            item["segment"] = door_seg(20.0, 20.0, 20.9, 20.0)
    variants["unknown_space_near_door"] = unknown

    leak_geom = unary_union([walk, rect(3.5, 1.5, 4.5, 2.5)])
    leaky = copy.deepcopy(tiny)
    leaky["walkable"] = {"L1": {"polygons": geometry_to_rings(leak_geom)}}
    leaky["navmesh"] = build_navmesh([("L1", 0.0, leak_geom)], 0.25)
    variants["leaky_wall_suspected"] = round_tree(leaky)

    orphan = copy.deepcopy(tiny)
    orphan["stairs"] = [
        {
            "id": "s_099",
            "from_level": "L1",
            "to_level": "L1",
            "polygon": ring_coords(rect(20.0, 20.0, 22.0, 22.0).exterior),
            "bottom": [21.0, 21.0],
            "top": [21.0, 21.5],
            "width_m": 1.2,
            "direction": "both",
            "is_enclosed": True,
        }
    ]
    variants["orphan_stair"] = orphan
    return variants


def main() -> None:
    tiny, parts = tiny_parts()
    write_scene(FIXTURES / "tiny_office.scene.json", tiny)
    school = school_scene()
    write_scene(FIXTURES / "two_floor_school.scene.json", school)
    expected_severity = {
        "no_exits": "error",
        "room_unreachable": "error",
        "exit_unreachable": "error",
        "door_too_narrow": "error",
        "small_room_artifact": "warn",
        "scale_low_confidence": "warn",
        "unknown_space_near_door": "warn",
        "leaky_wall_suspected": "warn",
        "orphan_stair": "warn",
    }
    codes = {
        "no_exits": "NO_EXITS",
        "room_unreachable": "ROOM_UNREACHABLE",
        "exit_unreachable": "EXIT_UNREACHABLE",
        "door_too_narrow": "DOOR_TOO_NARROW",
        "small_room_artifact": "SMALL_ROOM_ARTIFACT",
        "scale_low_confidence": "SCALE_LOW_CONFIDENCE",
        "unknown_space_near_door": "UNKNOWN_SPACE_NEAR_DOOR",
        "leaky_wall_suspected": "LEAKY_WALL_SUSPECTED",
        "orphan_stair": "ORPHAN_STAIR",
    }
    for name, scene in negative_variants(tiny, parts["L1"]).items():
        path = NEGATIVE / f"{name}.scene.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            Scene.model_validate(scene)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"{name} schema/model failed: {exc}") from exc
        path.write_text(json.dumps(round_tree(scene), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        issues = validate_data(json.loads(path.read_text(encoding="utf-8")))
        found = [issue for issue in issues if issue["code"] == codes[name]]
        if not found:
            raise SystemExit(f"{name} did not emit {codes[name]}:\n" + json.dumps(issues, indent=2))
        errors = [issue for issue in issues if issue["severity"] == "error"]
        if expected_severity[name] == "error" and not errors:
            raise SystemExit(f"{name} should fail and did not")
        if expected_severity[name] == "warn" and errors:
            raise SystemExit(f"{name} should only warn:\n" + json.dumps(issues, indent=2))
        print(f"ok {name}: {codes[name]}")
    print("fixtures written")


if __name__ == "__main__":
    main()
