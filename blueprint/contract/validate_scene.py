"""Validate a scene.json file against the frozen contract.

CLI: ``python -m contract.validate_scene scene.json``

Exit code is 0 only when no issue has severity ``error``.
Coordinates are meters in the XZ floor plane.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import networkx as nx
import numpy as np
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from contract.models import Scene

SCHEMA_PATH = Path(__file__).with_name("scene.schema.json")
MIN_POLYGON_AREA_M2 = 0.01
MIN_EXTENT_M = 3.0
MAX_EXTENT_M = 500.0
MIN_DOOR_M = 0.6
MAX_DOOR_M = 3.0
SMALL_ROOM_M2 = 1.0
DOOR_NEAR_M = 0.3
EXIT_SNAP_M = 0.5
NAVMESH_AREA_TOL = 0.02
MIN_TRI_AREA_M2 = 1e-6


class Issue(dict):
    """JSON-serializable validation issue."""

    def __init__(self, code: str, message: str, severity: str, ref: str | None = None) -> None:
        super().__init__(code=code, message=message, severity=severity, ref=ref)


def _ring_polygon(ring: list, holes: list | None = None) -> Polygon:
    return Polygon(ring, holes or [])


def _as_polygon(ring: list, holes: list | None = None) -> Polygon | None:
    if len(ring) < 3:
        return None
    try:
        poly = _ring_polygon(ring, holes)
    except (TypeError, ValueError):
        return None
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.geom_type != "Polygon":
        return None
    return poly


def _collect_points(scene: Scene) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []

    def add_ring(ring: list) -> None:
        for pt in ring:
            if len(pt) >= 2:
                points.append((float(pt[0]), float(pt[1])))

    for wall in scene.walls:
        add_ring(wall.polygon)
    for room in scene.rooms:
        add_ring(room.polygon)
    for door in scene.doors:
        add_ring(door.segment)
    for stair in scene.stairs:
        add_ring(stair.polygon)
        points.append((stair.bottom[0], stair.bottom[1]))
        points.append((stair.top[0], stair.top[1]))
    for obstacle in scene.obstacles:
        add_ring(obstacle.polygon)
    for exit_ in scene.exits:
        points.append((exit_.position[0], exit_.position[1]))
    for level_walk in scene.walkable.values():
        for poly in level_walk.polygons:
            add_ring(poly.exterior)
            for hole in poly.holes:
                add_ring(hole)
    return points


def _walkable_geom(scene: Scene, level_id: str):
    level = scene.walkable.get(level_id)
    if level is None:
        return Polygon()
    parts = []
    for poly in level.polygons:
        geom = _as_polygon(poly.exterior, poly.holes)
        if geom is not None and geom.area > 0:
            parts.append(geom)
    if not parts:
        return Polygon()
    return unary_union(parts)


def _room_polygon(room) -> Polygon | None:
    return _as_polygon(room.polygon)


def _containing_room(scene: Scene, level_id: str, position: list[float], tol_m: float = 0.05):
    point = Point(float(position[0]), float(position[1]))
    for room in scene.rooms:
        if room.level != level_id:
            continue
        poly = _room_polygon(room)
        if poly is None:
            continue
        if poly.buffer(tol_m).covers(point):
            return room
    return None


def _check_polygons(scene: Scene, issues: list[Issue]) -> None:
    items: list[tuple[str, list, list | None]] = []
    for wall in scene.walls:
        items.append((wall.id, wall.polygon, None))
    for room in scene.rooms:
        items.append((room.id, room.polygon, None))
    for stair in scene.stairs:
        items.append((stair.id, stair.polygon, None))
    for obstacle in scene.obstacles:
        items.append((obstacle.id, obstacle.polygon, None))
    for level_id, level_walk in scene.walkable.items():
        for index, poly in enumerate(level_walk.polygons):
            items.append((f"walkable:{level_id}:{index}", poly.exterior, poly.holes))
    for ref, ring, holes in items:
        poly = _as_polygon(ring, holes)
        if poly is None or not poly.is_valid or poly.area <= MIN_POLYGON_AREA_M2:
            area = 0.0 if poly is None else float(poly.area)
            issues.append(
                Issue(
                    "POLYGON_INVALID",
                    f"{ref} is not a valid polygon with area > {MIN_POLYGON_AREA_M2} m^2 (area={area:.4f})",
                    "error",
                    ref,
                )
            )


def _check_extent(scene: Scene, issues: list[Issue]) -> None:
    points = _collect_points(scene)
    if len(points) < 2:
        issues.append(Issue("EXTENT_INVALID", "Scene has no measurable extent", "error"))
        return
    xs = [p[0] for p in points]
    zs = [p[1] for p in points]
    longest = max(max(xs) - min(xs), max(zs) - min(zs))
    if longest < MIN_EXTENT_M or longest > MAX_EXTENT_M:
        issues.append(
            Issue(
                "EXTENT_INVALID",
                f"Longest side is {longest:.2f} m; expected {MIN_EXTENT_M:.0f} to {MAX_EXTENT_M:.0f} m",
                "error",
            )
        )


def _check_references(scene: Scene, issues: list[Issue]) -> None:
    level_ids = {level.id for level in scene.levels}
    room_ids = {room.id for room in scene.rooms}
    door_ids = {door.id for door in scene.doors}
    all_ids: list[str] = []
    for collection in (
        scene.levels,
        scene.walls,
        scene.rooms,
        scene.doors,
        scene.exits,
        scene.stairs,
        scene.elevators,
        scene.obstacles,
        scene.points_of_interest,
        scene.links,
    ):
        for item in collection:
            all_ids.append(item.id)
    seen: set[str] = set()
    for item_id in all_ids:
        if item_id in seen:
            issues.append(Issue("DUPLICATE_ID", f"Duplicate id {item_id}", "error", item_id))
        seen.add(item_id)

    def need_level(level_id: str, ref: str) -> None:
        if level_id not in level_ids:
            issues.append(Issue("BAD_REFERENCE", f"{ref} references unknown level {level_id}", "error", ref))

    for wall in scene.walls:
        need_level(wall.level, wall.id)
    for room in scene.rooms:
        need_level(room.level, room.id)
    for door in scene.doors:
        need_level(door.level, door.id)
        if len(door.connects) != 2:
            issues.append(Issue("BAD_REFERENCE", f"{door.id} must connect exactly two spaces", "error", door.id))
        for ref in door.connects:
            if ref != "outside" and ref not in room_ids:
                issues.append(Issue("BAD_REFERENCE", f"{door.id} connects unknown room {ref}", "error", door.id))
    for exit_ in scene.exits:
        need_level(exit_.level, exit_.id)
        if exit_.door_id not in door_ids:
            issues.append(Issue("BAD_REFERENCE", f"{exit_.id} references unknown door {exit_.door_id}", "error", exit_.id))
    for stair in scene.stairs:
        need_level(stair.from_level, stair.id)
        need_level(stair.to_level, stair.id)
    for elevator in scene.elevators:
        for level_id in elevator.levels:
            need_level(level_id, elevator.id)
    for obstacle in scene.obstacles:
        need_level(obstacle.level, obstacle.id)
    for poi in scene.points_of_interest:
        need_level(poi.level, poi.id)
    for link in scene.links:
        need_level(link.from_.level, link.id)
        need_level(link.to.level, link.id)
    for level_id in scene.walkable:
        if level_id not in level_ids:
            issues.append(Issue("BAD_REFERENCE", f"walkable key {level_id} is not a level", "error", level_id))
    for level_id in scene.navmesh.triangle_level:
        if level_id not in level_ids:
            issues.append(Issue("BAD_REFERENCE", f"navmesh triangle level {level_id} is unknown", "error"))


def _door_pair_key(connects: list[str]) -> frozenset[str] | None:
    rooms = [ref for ref in connects if ref != "outside"]
    if len(rooms) == 2:
        return frozenset(rooms)
    return None


def _build_graph(scene: Scene, issues: list[Issue]) -> nx.Graph:
    graph = nx.Graph()
    for room in scene.rooms:
        graph.add_node(room.id, kind="room")

    doors_by_id = {door.id: door for door in scene.doors}
    for door in scene.doors:
        ends: list[str] = []
        for ref in door.connects:
            if ref == "outside":
                node = f"outside:{door.id}"
                graph.add_node(node, kind="outside")
                ends.append(node)
            elif ref in graph:
                ends.append(ref)
        if len(ends) == 2:
            graph.add_edge(ends[0], ends[1], door_id=door.id)

    for exit_ in scene.exits:
        door = doors_by_id.get(exit_.door_id)
        exit_node = f"exit:{exit_.id}"
        graph.add_node(exit_node, kind="exit")
        if door is None:
            continue
        linked_room = False
        for ref in door.connects:
            if ref != "outside" and ref in graph:
                graph.add_edge(ref, exit_node, door_id=door.id)
                linked_room = True
        if not linked_room:
            issues.append(
                Issue(
                    "EXIT_UNREACHABLE",
                    f"Exit {exit_.id} door {exit_.door_id} is not incident to a room",
                    "error",
                    exit_.id,
                )
            )

    for stair in scene.stairs:
        bottom_room = _containing_room(scene, stair.from_level, stair.bottom)
        top_room = _containing_room(scene, stair.to_level, stair.top)
        if bottom_room is None or top_room is None:
            missing = stair.from_level if bottom_room is None else stair.to_level
            issues.append(
                Issue(
                    "ORPHAN_STAIR",
                    f"Stair {stair.id} endpoint is not inside a room on {missing}",
                    "warn",
                    stair.id,
                )
            )
            continue
        graph.add_edge(bottom_room.id, top_room.id, stair_id=stair.id)

    for link in scene.links:
        start_room = _containing_room(scene, link.from_.level, link.from_.position)
        end_room = _containing_room(scene, link.to.level, link.to.position)
        if start_room is None or end_room is None:
            issues.append(
                Issue(
                    "ORPHAN_STAIR",
                    f"Link {link.id} endpoint is not inside a room",
                    "warn",
                    link.id,
                )
            )
            continue
        graph.add_edge(start_room.id, end_room.id, link_id=link.id)
    return graph


def _check_doors_and_rooms(scene: Scene, issues: list[Issue]) -> None:
    widths = [door.width_m for door in scene.doors]
    for door in scene.doors:
        if door.width_m < MIN_DOOR_M or door.width_m > MAX_DOOR_M:
            issues.append(
                Issue(
                    "DOOR_TOO_NARROW",
                    f"Door {door.id} width {door.width_m:.3f} m is outside {MIN_DOOR_M:.1f}-{MAX_DOOR_M:.1f} m",
                    "error",
                    door.id,
                )
            )
        segment = door.segment
        if len(segment) >= 2:
            line = LineString([(float(p[0]), float(p[1])) for p in segment])
            for ref in door.connects:
                if ref == "outside":
                    continue
                room = next((item for item in scene.rooms if item.id == ref), None)
                if room is None:
                    continue
                poly = _room_polygon(room)
                if poly is None:
                    continue
                if line.distance(poly.boundary) > DOOR_NEAR_M:
                    issues.append(
                        Issue(
                            "UNKNOWN_SPACE_NEAR_DOOR",
                            f"Door {door.id} is {line.distance(poly.boundary):.2f} m from room {ref}",
                            "warn",
                            door.id,
                        )
                    )
    if widths:
        median = float(np.median(np.array(widths, dtype=float)))
        if median < 0.7 or median > 1.3:
            issues.append(
                Issue(
                    "SCALE_LOW_CONFIDENCE",
                    f"Median door width {median:.3f} m is outside 0.7-1.3 m",
                    "warn",
                )
            )
    for room in scene.rooms:
        poly = _room_polygon(room)
        if poly is None:
            continue
        if poly.area < SMALL_ROOM_M2:
            issues.append(
                Issue(
                    "SMALL_ROOM_ARTIFACT",
                    f"Room {room.id} area {poly.area:.3f} m^2 is under {SMALL_ROOM_M2:.0f} m^2",
                    "warn",
                    room.id,
                )
            )


def _check_exits_and_connectivity(scene: Scene, issues: list[Issue], graph: nx.Graph) -> None:
    if not scene.exits:
        issues.append(Issue("NO_EXITS", "Scene has no exits", "error"))
        return
    exit_nodes = [node for node, data in graph.nodes(data=True) if data.get("kind") == "exit"]
    reachable: set[str] = set()
    for node in exit_nodes:
        reachable |= nx.node_connected_component(graph, node)
    for room in scene.rooms:
        if room.id not in reachable:
            issues.append(
                Issue(
                    "ROOM_UNREACHABLE",
                    f"Room {room.id} cannot reach an exit",
                    "error",
                    room.id,
                )
            )


def _check_leaks(scene: Scene, issues: list[Issue]) -> None:
    doors_between: set[frozenset[str]] = set()
    for door in scene.doors:
        key = _door_pair_key(door.connects)
        if key is not None:
            doors_between.add(key)
    by_level: dict[str, list] = {}
    for room in scene.rooms:
        by_level.setdefault(room.level, []).append(room)
    for level_id, rooms in by_level.items():
        walkable = _walkable_geom(scene, level_id)
        if walkable.is_empty:
            continue
        regions = []
        for room in rooms:
            poly = _room_polygon(room)
            if poly is None:
                continue
            # 0.30 m reaches across a normal wall so a walkable bridge in the
            # wall gap is visible to both rooms. Eroded room interiors sit
            # 0.25 m inside the boundary, so a solid wall still keeps them apart.
            region = walkable.intersection(poly.buffer(0.30))
            regions.append((room, region))
        for index, (room_a, region_a) in enumerate(regions):
            if region_a.is_empty:
                continue
            for room_b, region_b in regions[index + 1 :]:
                if region_b.is_empty:
                    continue
                if frozenset((room_a.id, room_b.id)) in doors_between:
                    continue
                if region_a.buffer(0.02).intersects(region_b):
                    issues.append(
                        Issue(
                            "LEAKY_WALL_SUSPECTED",
                            f"Walkable area connects {room_a.id} and {room_b.id} without a door",
                            "warn",
                            room_a.id,
                        )
                    )


def _triangle_area(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    return float(0.5 * np.linalg.norm(np.cross(b - a, c - a)))


def _check_navmesh(scene: Scene, issues: list[Issue]) -> None:
    nav = scene.navmesh
    if len(nav.triangles) != len(nav.triangle_level):
        issues.append(
            Issue(
                "NAVMESH_INVALID",
                "triangle_level length does not match triangles",
                "error",
            )
        )
        return
    vertices = np.array(nav.vertices, dtype=float)
    if vertices.ndim != 2 or vertices.shape[1] != 3:
        issues.append(Issue("NAVMESH_INVALID", "Navmesh vertices must be [x, y, z]", "error"))
        return
    count = len(vertices)
    triangles = []
    for index, tri in enumerate(nav.triangles):
        if len(tri) != 3 or any(int(v) < 0 or int(v) >= count for v in tri):
            issues.append(Issue("NAVMESH_INVALID", f"Triangle {index} has an out-of-range index", "error"))
            return
        triangles.append((int(tri[0]), int(tri[1]), int(tri[2])))
        area = _triangle_area(vertices[tri[0]], vertices[tri[1]], vertices[tri[2]])
        if area < MIN_TRI_AREA_M2:
            issues.append(Issue("NAVMESH_INVALID", f"Triangle {index} is degenerate", "error"))
            return

    walkable_area = 0.0
    for level_walk in scene.walkable.values():
        for poly in level_walk.polygons:
            geom = _as_polygon(poly.exterior, poly.holes)
            if geom is not None:
                walkable_area += float(geom.area)
    tri_area = 0.0
    for a, b, c in triangles:
        tri_area += _triangle_area(vertices[a], vertices[b], vertices[c])
    if walkable_area <= 0:
        issues.append(Issue("NAVMESH_INVALID", "Walkable area is empty", "error"))
        return
    if abs(tri_area - walkable_area) / walkable_area > NAVMESH_AREA_TOL:
        issues.append(
            Issue(
                "NAVMESH_INVALID",
                f"Navmesh area {tri_area:.3f} m^2 differs from walkable {walkable_area:.3f} m^2 by more than 2%",
                "error",
            )
        )

    adjacency: dict[int, set[int]] = {i: set() for i in range(len(triangles))}
    edge_map: dict[tuple[int, int], int] = {}
    for index, (a, b, c) in enumerate(triangles):
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            other = edge_map.get(key)
            if other is None:
                edge_map[key] = index
            else:
                adjacency[index].add(other)
                adjacency[other].add(index)

    def triangles_near(position: list[float], level_id: str) -> list[int]:
        point = Point(float(position[0]), float(position[1]))
        hits: list[int] = []
        for index, (a, b, c) in enumerate(triangles):
            if nav.triangle_level[index] != level_id:
                continue
            pts = vertices[[a, b, c]]
            tri_poly = Polygon([(pts[0, 0], pts[0, 2]), (pts[1, 0], pts[1, 2]), (pts[2, 0], pts[2, 2])])
            if tri_poly.distance(point) <= EXIT_SNAP_M:
                hits.append(index)
        return hits

    def bridge_components(start: list[float], start_level: str, end: list[float], end_level: str) -> None:
        starts = triangles_near(start, start_level)
        ends = triangles_near(end, end_level)
        for left in starts:
            for right in ends:
                adjacency[left].add(right)
                adjacency[right].add(left)

    for stair in scene.stairs:
        bridge_components(stair.bottom, stair.from_level, stair.top, stair.to_level)
    for link in scene.links:
        bridge_components(link.from_.position, link.from_.level, link.to.position, link.to.level)

    exit_triangles: set[int] = set()
    for exit_ in scene.exits:
        exit_point = Point(float(exit_.position[0]), float(exit_.position[1]))
        for index, (a, b, c) in enumerate(triangles):
            if nav.triangle_level[index] != exit_.level:
                continue
            pts = vertices[[a, b, c]]
            tri_poly = Polygon([(pts[0, 0], pts[0, 2]), (pts[1, 0], pts[1, 2]), (pts[2, 0], pts[2, 2])])
            if tri_poly.distance(exit_point) <= EXIT_SNAP_M:
                exit_triangles.add(index)

    if scene.exits and triangles and not exit_triangles:
        issues.append(
            Issue(
                "EXIT_UNREACHABLE",
                "No navmesh triangle is within 0.5 m of an exit",
                "error",
            )
        )
        return

    seen: set[int] = set()
    for start in range(len(triangles)):
        if start in seen:
            continue
        stack = [start]
        component: list[int] = []
        seen.add(start)
        while stack:
            current = stack.pop()
            component.append(current)
            for nxt in adjacency[current]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        if not any(index in exit_triangles for index in component):
            issues.append(
                Issue(
                    "NAVMESH_INVALID",
                    "A navmesh component does not touch an exit",
                    "error",
                )
            )
            return


def validate_data(data: dict) -> list[Issue]:
    """Validate a decoded scene document. Returns issues; does not raise."""

    issues: list[Issue] = []
    if SCHEMA_PATH.is_file():
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for error in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
            path = "/".join(str(part) for part in error.path) or "(root)"
            issues.append(Issue("SCHEMA", f"{path}: {error.message}", "error"))
        if issues:
            return issues
    try:
        scene = Scene.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors():
            path = "/".join(str(part) for part in error["loc"])
            issues.append(Issue("SCHEMA", f"{path}: {error['msg']}", "error"))
        return issues

    _check_references(scene, issues)
    _check_polygons(scene, issues)
    _check_extent(scene, issues)
    _check_doors_and_rooms(scene, issues)
    graph = _build_graph(scene, issues)
    _check_exits_and_connectivity(scene, issues, graph)
    _check_leaks(scene, issues)
    _check_navmesh(scene, issues)
    return issues


def validate_path(path: Path) -> list[Issue]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [Issue("SCHEMA", f"Invalid JSON: {exc}", "error")]
    if not isinstance(data, dict):
        return [Issue("SCHEMA", "Scene root must be an object", "error")]
    return validate_data(data)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m contract.validate_scene scene.json", file=sys.stderr)
        return 2
    issues = validate_path(Path(args[0]))
    print(json.dumps(issues, indent=2))
    errors = [issue for issue in issues if issue["severity"] == "error"]
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
