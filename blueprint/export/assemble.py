"""Build a scene.json document from detected meter geometry.

Room ids are assigned in reading order (z, then x) so the same geometry always
produces the same ids. ``generated_at`` is the only timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from geometry.polygons import geometry_to_rings, ring_coords, round_tree
from geometry.walkable import build_walkable
from nav.connectivity import exit_distances
from nav.navmesh import build_navmesh


def _centroid(poly: Polygon) -> list[float]:
    point = poly.centroid
    return [float(point.x), float(point.y)]


def assemble_scene(
    levels: list[dict],
    meta_name: str,
    source_file: str,
    scale_method: str,
    scale_confidence: float,
    generated_by: str,
    source: str = "blueprint",
) -> tuple[dict, list]:
    """Assemble a scene.

    Each level dict contains ``level_id``, ``name``, ``elevation_m``,
    ``ceiling_height_m``, ``walls`` (Segment), ``doors`` (DoorOpening),
    ``faces`` (Polygon), ``labels`` (name, type), ``obstacles`` (Polygon),
    and ``stair_indexes`` (set of face indexes).
    """

    scene_levels = []
    walls = []
    rooms = []
    doors = []
    exits = []
    stairs = []
    links = []
    obstacles = []
    walkable_geoms = []
    elevations = {}

    for level in levels:
        level_id = level["level_id"]
        elevations[level_id] = level["elevation_m"]
        scene_levels.append(
            {
                "id": level_id,
                "name": level["name"],
                "elevation_m": level["elevation_m"],
                "ceiling_height_m": level["ceiling_height_m"],
            }
        )
        face_ids = []
        order = sorted(
            range(len(level["faces"])),
            key=lambda index: (
                round(_centroid(level["faces"][index])[1], 3),
                round(_centroid(level["faces"][index])[0], 3),
            ),
        )
        id_of = {}
        for slot, index in enumerate(order, start=1):
            room_id = f"r_{level_id}_{slot:03d}"
            id_of[index] = room_id
            face_ids.append(room_id)
            face = level["faces"][index]
            name, room_type = level["labels"][index]
            if index in level.get("stair_indexes", set()):
                room_type = "stairwell"
                if name in {"Room", "unknown"}:
                    name = "Stair"
            rooms.append(
                {
                    "id": room_id,
                    "level": level_id,
                    "name": name,
                    "type": room_type,
                    "polygon": ring_coords(face.exterior),
                    "area_m2": float(face.area),
                    "centroid": _centroid(face),
                    "has_window": False,
                }
            )
        wall_index = 1
        wall_polys = []
        for segment in level["walls"]:
            line = LineString([segment.p1, segment.p2])
            if line.length < 0.05:
                continue
            poly = line.buffer(max(segment.thickness_m, 0.1) / 2.0, cap_style=2)
            if poly.is_empty or poly.geom_type != "Polygon" or poly.area <= 0.01:
                continue
            wall_polys.append(poly)
            walls.append(
                {
                    "id": f"w_{level_id}_{wall_index:03d}",
                    "level": level_id,
                    "polygon": ring_coords(poly.exterior),
                    "height_m": level["ceiling_height_m"],
                }
            )
            wall_index += 1
        door_index = 1
        door_segments = []
        single_level = len(levels) == 1
        for door in level["doors"]:
            hits = door.hits
            if not hits:
                continue
            connects = []
            for hit in hits:
                connects.append(id_of.get(hit, "outside"))
            if len(hits) == 1:
                connects.append("outside")
            if len(connects) != 2:
                continue
            door_id = f"d_{level_id}_{door_index:03d}"
            door_index += 1
            is_exit = len(hits) == 1 or door.is_exit
            leads_to = "outside"
            if any(level["faces"][hit].area > 0 and id_of.get(hit) and _is_stair(level, hit) for hit in hits):
                if single_level or door.leads_to == "stairwell":
                    is_exit = True
                    leads_to = "stairwell"
            if len(hits) == 1:
                is_exit = True
                leads_to = "outside"
            doors.append(
                {
                    "id": door_id,
                    "level": level_id,
                    "segment": [list(door.p1), list(door.p2)],
                    "width_m": float(door.width_m),
                    "connects": connects,
                    "is_exit": is_exit,
                    "kind": door.kind,
                    "fire_rated": None,
                    "opens_toward": connects[0],
                }
            )
            door_segments.append([door.p1, door.p2])
            if is_exit:
                room_centroid = _centroid(level["faces"][hits[0]])
                mid = door.midpoint
                outward = (mid[0] - room_centroid[0], mid[1] - room_centroid[1])
                norm = (outward[0] ** 2 + outward[1] ** 2) ** 0.5 or 1.0
                assembly = [mid[0] + outward[0] / norm * 2.0, mid[1] + outward[1] / norm * 2.0]
                exits.append(
                    {
                        "id": f"e_{level_id}_{len(exits) + 1:03d}",
                        "level": level_id,
                        "door_id": door_id,
                        "position": [mid[0], mid[1]],
                        "width_m": float(door.width_m),
                        "leads_to": leads_to,
                        "label": door.label or ("Stair" if leads_to == "stairwell" else "Exit"),
                        "assembly_point": assembly,
                    }
                )
        obstacle_polys = list(level.get("obstacles", []))
        for index, poly in enumerate(obstacle_polys, start=1):
            obstacles.append(
                {
                    "id": f"o_{level_id}_{index:03d}",
                    "level": level_id,
                    "polygon": ring_coords(poly.exterior),
                    "height_m": 0.8,
                    "kind": "furniture",
                    "passable": False,
                }
            )
        room_polys = list(level["faces"])
        walkable = build_walkable(
            room_polys,
            wall_polys,
            obstacle_polys,
            door_segments,
            wall_thickness_m=level.get("door_depth_m", 0.2),
            agent_radius_m=0.25,
        )
        walkable_geoms.append((level_id, level["elevation_m"], walkable))

    _add_stair_links(levels, rooms, stairs, links)
    walkable_json = {}
    nav_input = []
    for level_id, elevation, geom in walkable_geoms:
        simplified = geom.simplify(0.02, preserve_topology=True)
        if simplified.is_empty:
            simplified = geom
        walkable_json[level_id] = {"polygons": geometry_to_rings(simplified)}
        nav_input.append((level_id, elevation, simplified))
    scene = {
        "schema_version": "1.0",
        "meta": {
            "name": meta_name,
            "source": source,
            "source_file": source_file,
            "units": "meters",
            "up_axis": "y",
            "scale_method": scale_method,
            "scale_confidence": round(float(scale_confidence), 3),
            "generated_by": generated_by,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
        "levels": scene_levels,
        "walls": walls,
        "rooms": rooms,
        "doors": doors,
        "exits": exits,
        "stairs": stairs,
        "elevators": [],
        "obstacles": obstacles,
        "points_of_interest": [],
        "walkable": walkable_json,
        "navmesh": build_navmesh(nav_input, 0.25),
        "links": links,
        "assets": {
            "display_model": "display.glb",
            "floorplan_overlay": "overlay.png",
            "navmesh_debug": "navmesh_debug.glb",
        },
        "warnings": [],
        "extensions": {},
    }
    scene["extensions"]["exit_distance_m"] = exit_distances(scene)
    scene = round_tree(scene)
    _drop_degenerate_triangles(scene["navmesh"])
    return scene, walkable_geoms


def _is_stair(level: dict, face_index: int) -> bool:
    return face_index in level.get("stair_indexes", set())


def _add_stair_links(levels: list[dict], rooms: list[dict], stairs: list[dict], links: list[dict]) -> None:
    if len(levels) < 2:
        return
    by_level: dict[str, list[dict]] = {}
    for room in rooms:
        if room["type"] == "stairwell":
            by_level.setdefault(room["level"], []).append(room)
    for lower, upper in zip(levels, levels[1:]):
        lower_rooms = sorted(by_level.get(lower["level_id"], []), key=lambda room: room["centroid"][0])
        upper_rooms = sorted(by_level.get(upper["level_id"], []), key=lambda room: room["centroid"][0])
        for index, (start, end) in enumerate(zip(lower_rooms, upper_rooms), start=1):
            stair_id = f"s_{lower['level_id']}_{upper['level_id']}_{index:03d}"
            polygon = start["polygon"]
            bottom = start["centroid"]
            top = end["centroid"]
            stairs.append(
                {
                    "id": stair_id,
                    "from_level": lower["level_id"],
                    "to_level": upper["level_id"],
                    "polygon": polygon,
                    "bottom": bottom,
                    "top": top,
                    "width_m": 1.2,
                    "direction": "both",
                    "is_enclosed": True,
                }
            )
            links.append(
                {
                    "id": f"link_{lower['level_id']}_{upper['level_id']}_{index:03d}",
                    "type": "stairs",
                    "from": {"level": lower["level_id"], "position": bottom},
                    "to": {"level": upper["level_id"], "position": top},
                    "cost_multiplier": 2.0,
                    "bidirectional": True,
                }
            )


def _drop_degenerate_triangles(navmesh: dict) -> None:
    """Rounding vertices to 0.1 mm can collapse a few sliver triangles."""

    import numpy as np

    vertices = np.array(navmesh["vertices"], dtype=float)
    kept_tris = []
    kept_levels = []
    for triangle, level in zip(navmesh["triangles"], navmesh["triangle_level"]):
        a, b, c = vertices[triangle]
        area = 0.5 * float(np.linalg.norm(np.cross(b - a, c - a)))
        if area >= 1e-6:
            kept_tris.append(triangle)
            kept_levels.append(level)
    navmesh["triangles"] = kept_tris
    navmesh["triangle_level"] = kept_levels


def translated_ring(ring: list[list[float]], shift: tuple[float, float]) -> list[list[float]]:
    return [[point[0] + shift[0], point[1] + shift[1]] for point in ring]


def translate_polygon(poly: Polygon, shift: tuple[float, float]) -> Polygon:
    return Polygon([(x + shift[0], z + shift[1]) for x, z in poly.exterior.coords[:-1]])
