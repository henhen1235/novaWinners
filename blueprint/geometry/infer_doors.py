"""Doors for drawings that do not show them.

Space-management sheets (CMU ESIM exports) draw every room fully enclosed.
Without doors no room reaches an exit, so the scene is useless for egress.
When a page shows almost no doors, each group of rooms that cannot reach
outside gets one door on its longest shared wall with a reachable room, and a
page with no exit gets one on the longest exterior wall of its largest room.
Every door placed here has ``inferred=True`` so the scene can say so.
"""

from __future__ import annotations

from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

from geometry.rooms import attach_doors
from ingest_blueprint.types import DoorOpening

DOOR_M = 0.9
TOUCH_M = 0.3  # raster faces can sit a wall gap apart; stays inside attach_doors' 0.35 m
SPARSE_RATIO = 0.3


def needs_inference(doors: list[DoorOpening], faces: list) -> bool:
    return len(faces) > 1 and len(doors) < SPARSE_RATIO * len(faces)


def infer_doors(faces: list, doors: list[DoorOpening]) -> list[DoorOpening]:
    """Return ``doors`` plus inferred ones so every face can reach outside."""

    outside = len(faces)
    parent = list(range(len(faces) + 1))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def join(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for _door, hits in attach_doors(doors, faces):
        if len(hits) == 2:
            join(hits[0], hits[1])
        elif len(hits) == 1:
            join(hits[0], outside)
    def reachable(node: int) -> bool:
        return find(node) == find(outside)

    added: list[DoorOpening] = []
    if not any(reachable(i) for i in range(len(faces))):
        exit_door = _exit_on_largest(faces)
        if exit_door is not None:
            added.append(exit_door)
            join(exit_door.hits[0], outside)
    edges = sorted(_shared_walls(faces).items(), key=lambda item: -item[1].length)
    while True:
        progress = False
        for (i, j), line in edges:
            if reachable(i) != reachable(j):
                added.append(_door_on(line, False))
                join(i, j)
                progress = True
        if progress:
            continue
        # Unreachable groups that only border each other: join the pair with the longest wall, then retry.
        for (i, j), line in edges:
            if find(i) != find(j):
                added.append(_door_on(line, False))
                join(i, j)
                progress = True
                break
        if not progress:
            break
    return list(doors) + added


def doors_from_arcs(faces: list, arcs: list) -> list[DoorOpening]:
    """Doors from CAD swing arcs: (hinge, arc midpoint, radius) in metres.

    The hinge sits on a jamb, so the door lies on the shared wall nearest the
    hinge (or on a face's outer wall: an exit), running one radius from the
    hinge toward the side the arc swings over.
    """

    shared = _shared_walls(faces)
    grown = unary_union(faces).buffer(TOUCH_M) if faces else None
    # One outer ring per separate building part (the union can be a MultiPolygon).
    envelope = unary_union([part.exterior for part in getattr(grown, "geoms", [grown])]) if grown else None
    doors: list[DoorOpening] = []
    for hinge, middle, radius in arcs:
        hinge_point, middle_point = Point(hinge), Point(middle)
        reach = max(0.3, 0.5 * radius)
        best = min(shared.values(), key=lambda line: line.distance(hinge_point), default=None)
        is_exit = best is None or best.distance(hinge_point) > reach
        if is_exit:
            # Only a swing on the building's outer boundary is an exit; one facing a
            # space that is not a room (unclosed circulation) is left to inference.
            if envelope is None or envelope.distance(hinge_point) > reach + TOUCH_M:
                continue
            rings = [LineString(face.exterior.coords) for face in faces]
            best = min(rings, key=lambda ring: ring.distance(hinge_point), default=None)
            if best is None or best.distance(hinge_point) > reach:
                continue
        start = best.project(hinge_point)
        end = start + radius if best.project(middle_point) >= start else start - radius
        a = best.interpolate(start)
        b = best.interpolate(min(max(end, 0.0), best.length))
        door = DoorOpening((a.x, a.y), (b.x, b.y), a.distance(b), is_exit=is_exit)
        if door.width_m >= 0.5 and all(Point(door.midpoint).distance(Point(other.midpoint)) > 0.5 for other in doors):
            doors.append(door)
    return doors


def _shared_walls(faces: list) -> dict[tuple[int, int], LineString]:
    tree = STRtree(faces)
    shared: dict[tuple[int, int], LineString] = {}
    for i, face in enumerate(faces):
        for j in tree.query(face.buffer(TOUCH_M)):
            j = int(j)
            if j <= i:
                continue
            line = _longest(face.boundary.intersection(faces[j].buffer(TOUCH_M)))
            if line is not None and line.length >= DOOR_M + 0.2:
                shared[(i, j)] = line
    return shared


def _exit_on_largest(faces: list) -> DoorOpening | None:
    for index in sorted(range(len(faces)), key=lambda k: -faces[k].area):
        others = unary_union([face for k, face in enumerate(faces) if k != index]).buffer(TOUCH_M)
        line = _longest(faces[index].boundary.difference(others))
        if line is not None and line.length >= DOOR_M + 0.2:
            door = _door_on(line, True)
            door.hits = [index]
            return door
    return None


def _door_on(line: LineString, is_exit: bool) -> DoorOpening:
    middle = line.length / 2.0
    a = line.interpolate(max(0.0, middle - DOOR_M / 2.0))
    b = line.interpolate(min(line.length, middle + DOOR_M / 2.0))
    return DoorOpening(
        (a.x, a.y), (b.x, b.y), Point(a).distance(Point(b)), is_exit=is_exit,
        label="Exit (inferred)" if is_exit else "Exit", inferred=True,
    )


def _longest(geometry) -> LineString | None:
    if geometry.is_empty:
        return None
    if isinstance(geometry, LineString):
        return geometry
    parts = [part for part in getattr(geometry, "geoms", []) if isinstance(part, (LineString, MultiLineString))]
    lines = [line for part in parts for line in (part.geoms if isinstance(part, MultiLineString) else [part])]
    if not lines:
        return None
    from shapely.ops import linemerge

    merged = linemerge(lines)
    candidates = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    return max(candidates, key=lambda line: line.length)
