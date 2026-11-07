"""Synthetic non-Manhattan plan: a round room, a curved corridor and a 30-degree wing.

Built for Tepper-style buildings. Rooms are polygons in image metres (y down)
separated by 0.2 m walls; a door is a strip across a wall, painted white with
a swing arc. Writes ``plan.png`` and ``truth.scene.json`` through
``swiss_to_plan.render_plan``.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from tools.swiss_to_plan import render_plan

WALL_M = 0.2
DOOR_M = 0.9


def _sector(center, r_in: float, r_out: float, a0: float, a1: float, steps: int = 64) -> Polygon:
    angles = [math.radians(a0 + (a1 - a0) * i / steps) for i in range(steps + 1)]
    outer = [(center[0] + r_out * math.cos(a), center[1] + r_out * math.sin(a)) for a in angles]
    inner = [(center[0] + r_in * math.cos(a), center[1] + r_in * math.sin(a)) for a in reversed(angles)]
    return Polygon(outer + inner)


def _door(at, direction_deg: float) -> Polygon:
    """A strip DOOR_M wide across a wall, centred on ``at``, crossing along ``direction_deg``."""

    strip = box(-0.4, -DOOR_M / 2, 0.4, DOOR_M / 2)
    return affinity.translate(affinity.rotate(strip, direction_deg, origin=(0, 0)), *at)


def build():
    hall = box(2.0, 2.0, 10.0, 8.0)
    rotunda_c = (14.4, 5.0)
    rotunda = Point(rotunda_c).buffer(3.0, quad_segs=32)
    link = box(10.2, 4.4, 11.8, 5.6).difference(rotunda.buffer(WALL_M))
    ring = _sector(rotunda_c, 3.0 + WALL_M, 4.8, -70.0, 70.0)
    wing_raw = affinity.rotate(box(-4.0, 3.0, 2.2, 7.0), 30.0, origin=(2.0, 5.0))
    wing = wing_raw.difference(hall.buffer(WALL_M, join_style=2)).difference(box(1.8, -5, 30, 30))
    wing = max(getattr(wing, "geoms", [wing]), key=lambda g: g.area)
    rooms = [(hall, "Hall"), (rotunda, "Rotunda"), (link, "Link"), (ring, "Curved Corridor"), (wing, "Wing")]
    free = unary_union([room for room, _ in rooms])
    walls = free.buffer(WALL_M, join_style=2).difference(free)
    ring_mid = (rotunda_c[0] + 3.0 + WALL_M / 2, rotunda_c[1])
    end_angle = math.radians(-70.0)
    ring_end = (rotunda_c[0] + 4.0 * math.cos(end_angle), rotunda_c[1] + 4.0 * math.sin(end_angle))
    wing_far = affinity.rotate(Point(-4.0 + WALL_M / 2 - 0.2, 5.0), 30.0, origin=(2.0, 5.0))
    doors = [
        (_door((10.1, 5.0), 0.0), False),            # hall - link
        (_door((11.6 + 0.2, 5.0), 0.0), False),      # link - rotunda (curved wall)
        (_door(ring_mid, 0.0), False),               # rotunda - curved corridor (curved wall)
        (_door((1.9, 5.0), 0.0), False),             # hall - wing
        (_door((6.0, 1.9), 90.0), True),             # hall north exit
        (_door(ring_end, -70.0 + 90.0), True),       # curved corridor end exit (radial wall)
        (_door((wing_far.x, wing_far.y), 30.0), True),  # wing far exit (30 deg wall)
    ]
    doors = [(door.intersection(walls), is_exit) for door, is_exit in doors]
    return [walls], rooms, [(door, is_exit) for door, is_exit in doors if not door.is_empty]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--px-per-m", type=float, default=100.0)
    args = parser.parse_args()
    walls, rooms, doors = build()
    render_plan(walls, rooms, doors, [], args.out, args.px_per_m)
    print(args.out)


if __name__ == "__main__":
    main()
