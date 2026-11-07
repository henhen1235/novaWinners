"""Walk a navmesh to the nearest exit.

The mesh is a triangle graph in meters. Y is up and the plan is XZ, so distance
uses x and z. Time assumes 1.3 m/s walking speed.
"""

from __future__ import annotations

import math

import networkx as nx
import numpy as np
from shapely.geometry import Point, Polygon

WALK_SPEED_M_S = 1.3


def path_to_exit(navmesh: dict, start: tuple[float, float], exits: list[dict], level: str | None = None) -> dict:
    """Return ``path`` as [x, z] points, ``distance_m``, and ``time_s``."""

    vertices = np.array(navmesh["vertices"], dtype=float)
    triangles = navmesh["triangles"]
    levels = navmesh["triangle_level"]
    if len(triangles) == 0:
        return {"path": [], "distance_m": None, "time_s": None}
    centroids = []
    polys = []
    for index, tri in enumerate(triangles):
        pts = vertices[tri]
        centroid = pts.mean(axis=0)
        centroids.append((float(centroid[0]), float(centroid[2]), float(centroid[1])))
        polys.append(Polygon([(pts[0, 0], pts[0, 2]), (pts[1, 0], pts[1, 2]), (pts[2, 0], pts[2, 2])]))
    graph = nx.Graph()
    edge_owner: dict[tuple[int, int], int] = {}
    for index, tri in enumerate(triangles):
        graph.add_node(index)
        a, b, c = (int(v) for v in tri)
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            other = edge_owner.get(key)
            if other is None:
                edge_owner[key] = index
            else:
                weight = math.dist(centroids[index][:2], centroids[other][:2])
                graph.add_edge(index, other, weight=weight)
    start_point = Point(start[0], start[1])
    start_tri = _nearest(polys, levels, start_point, level)
    goals = []
    for exit_ in exits:
        if level and exit_["level"] != level:
            continue
        goals.extend(_near_exit(polys, levels, Point(exit_["position"][0], exit_["position"][1]), exit_["level"]))
    if start_tri is None or not goals:
        return {"path": [], "distance_m": None, "time_s": None}
    best = None
    best_node = None
    for goal in goals:
        if not nx.has_path(graph, start_tri, goal):
            continue
        length = nx.shortest_path_length(graph, start_tri, goal, weight="weight")
        if best is None or length < best:
            best = float(length)
            best_node = goal
    if best_node is None or best is None:
        return {"path": [], "distance_m": None, "time_s": None}
    nodes = nx.shortest_path(graph, start_tri, best_node, weight="weight")
    path = [list(start)]
    for node in nodes:
        path.append([centroids[node][0], centroids[node][1]])
    # Replace the centroid chain length with the polyline length so the printed
    # distance matches the drawn path.
    distance = 0.0
    for p1, p2 in zip(path, path[1:]):
        distance += math.dist(p1, p2)
    return {
        "path": [[round(point[0], 3), round(point[1], 3)] for point in path],
        "distance_m": round(distance, 3),
        "time_s": round(distance / WALK_SPEED_M_S, 2),
    }


def _nearest(polys: list[Polygon], levels: list[str], point: Point, level: str | None) -> int | None:
    best = None
    best_distance = None
    for index, poly in enumerate(polys):
        if level and levels[index] != level:
            continue
        distance = poly.distance(point)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = index
    return best


def _near_exit(polys: list[Polygon], levels: list[str], point: Point, level: str) -> list[int]:
    hits = []
    for index, poly in enumerate(polys):
        if levels[index] != level:
            continue
        if poly.distance(point) <= 0.75:
            hits.append(index)
    if hits:
        return hits
    nearest = _nearest(polys, levels, point, level)
    return [] if nearest is None else [nearest]
