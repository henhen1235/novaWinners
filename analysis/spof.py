"""Single points of failure on the room-door graph.

Nodes: rooms plus "outside"; edges: doors (and stairs between the stairwell
rooms they land in). A MultiGraph keeps two exits joining the same pair of
nodes as two edges, so neither looks like a bridge. Each door and each room is
blocked in turn (brute force, O(n * (V + E)), fine for one building) and we
record which other rooms lose every path to outside and how much of the total
exit width is lost with it.
"""

from __future__ import annotations

import networkx as nx
from shapely.geometry import Point, Polygon

OUT = "outside"


def _room_at(scene: dict, level: str, pt: list[float]) -> str | None:
    for r in scene["rooms"]:
        if r["level"] == level and Polygon(r["polygon"]).buffer(0.3).contains(Point(pt)):
            return r["id"]
    return None


def build_graph(scene: dict) -> nx.MultiGraph:
    g = nx.MultiGraph()
    g.add_nodes_from([r["id"] for r in scene["rooms"]] + [OUT])
    for d in scene["doors"]:
        a, b = d["connects"]
        if a in g and b in g:
            g.add_edge(a, b, key=d["id"], width_m=d["width_m"])
    for s in scene.get("stairs", []):
        a, b = _room_at(scene, s["from_level"], s["bottom"]), _room_at(scene, s["to_level"], s["top"])
        if a and b:
            g.add_edge(a, b, key=s["id"], width_m=s["width_m"])
    return g


def _stranded(g: nx.MultiGraph, blocked_room: str | None = None) -> list[str]:
    """Rooms (other than a blocked one) with no path to outside."""
    if blocked_room:
        g = g.subgraph([n for n in g if n != blocked_room])
    safe = nx.node_connected_component(g, OUT)
    return sorted(n for n in g if n not in safe)


def analyze(scene: dict, loads: dict[str, int] | None = None) -> dict:
    loads = loads or {}
    g = build_graph(scene)
    exit_w = {e["door_id"]: e["width_m"] for e in scene["exits"]}
    total_w = sum(exit_w.values()) or 1.0
    door_rooms = {d["id"]: d["connects"] for d in scene["doors"]}
    baseline = set(_stranded(g))
    rows = []
    for d in scene["doors"]:
        a, b = d["connects"]
        if not g.has_edge(a, b, key=d["id"]):
            continue
        h = g.copy()
        h.remove_edge(a, b, key=d["id"])
        stranded = [r for r in _stranded(h) if r not in baseline]
        lost = exit_w.get(d["id"], 0.0)
        rows.append({"kind": "door", "id": d["id"], "stranded_rooms": stranded,
                     "leaf": len(stranded) == 1 and g.degree(stranded[0]) == 1,
                     "exit_width_lost_m": round(lost, 3)})
    for r in scene["rooms"]:
        stranded = [x for x in _stranded(g, r["id"]) if x not in baseline]
        lost = sum(w for door, w in exit_w.items() if r["id"] in door_rooms.get(door, []))
        rows.append({"kind": "room", "id": r["id"], "stranded_rooms": stranded, "leaf": False,
                     "exit_width_lost_m": round(lost, 3)})
    for row in rows:
        row["stranded_occupants"] = sum(loads.get(x, 0) for x in row["stranded_rooms"])
        row["exit_width_lost_share"] = round(row["exit_width_lost_m"] / total_w, 3)
        row["exit_width_left_share"] = round(1 - row["exit_width_lost_share"], 3)
    # A one-door room's own door strands only that room: real, but obvious, so it ranks last.
    ranked = sorted((r for r in rows if r["stranded_rooms"] or r["exit_width_lost_m"] > 0),
                    key=lambda r: (not r["leaf"], len(r["stranded_rooms"]), r["stranded_occupants"],
                                   r["exit_width_lost_share"]), reverse=True)
    simple = nx.Graph(g)
    return {"articulation_rooms": sorted(n for n in nx.articulation_points(simple) if n != OUT),
            "bridge_doors": [r["id"] for r in rows if r["kind"] == "door" and r["stranded_rooms"]],
            "total_exit_width_m": round(total_w, 3),
            "unreachable_rooms": sorted(baseline),
            "ranked": ranked}
