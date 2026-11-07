"""Room graph and travel distance to the nearest exit.

Edge weight is the straight-line distance in meters between room centroids,
or from a centroid to an exit position. Stair links use ``cost_multiplier``.
"""

from __future__ import annotations

import math

import networkx as nx


def _dist(a: list[float] | tuple[float, float], b: list[float] | tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def exit_distances(scene: dict) -> dict[str, float]:
    """Shortest centroid-to-exit distance in meters for each room. Missing rooms are omitted."""

    graph = nx.Graph()
    centroids = {room["id"]: room["centroid"] for room in scene["rooms"]}
    for room_id in centroids:
        graph.add_node(room_id)
    exit_nodes = []
    doors = {door["id"]: door for door in scene["doors"]}
    for exit_ in scene["exits"]:
        node = f"exit:{exit_['id']}"
        exit_nodes.append(node)
        graph.add_node(node)
        door = doors.get(exit_["door_id"])
        if door is None:
            continue
        for ref in door["connects"]:
            if ref in centroids:
                graph.add_edge(ref, node, weight=_dist(centroids[ref], exit_["position"]))
    for door in scene["doors"]:
        rooms = [ref for ref in door["connects"] if ref in centroids]
        if len(rooms) == 2:
            graph.add_edge(rooms[0], rooms[1], weight=_dist(centroids[rooms[0]], centroids[rooms[1]]))
    for link in scene.get("links", []):
        start = _room_at(scene, link["from"]["level"], link["from"]["position"])
        end = _room_at(scene, link["to"]["level"], link["to"]["position"])
        if start and end:
            weight = _dist(link["from"]["position"], link["to"]["position"]) * float(link.get("cost_multiplier", 1.0))
            weight = max(weight, 1.0)
            graph.add_edge(start, end, weight=weight)
    distances: dict[str, float] = {}
    for room_id in centroids:
        best = None
        for exit_node in exit_nodes:
            if not nx.has_path(graph, room_id, exit_node):
                continue
            length = nx.shortest_path_length(graph, room_id, exit_node, weight="weight")
            if best is None or length < best:
                best = float(length)
        if best is not None:
            distances[room_id] = round(best, 3)
    return distances


def _room_at(scene: dict, level: str, position: list[float]) -> str | None:
    from shapely.geometry import Point, Polygon

    point = Point(float(position[0]), float(position[1]))
    for room in scene["rooms"]:
        if room["level"] != level:
            continue
        polygon = Polygon(room["polygon"])
        if polygon.buffer(0.2).covers(point):
            return room["id"]
    return None
