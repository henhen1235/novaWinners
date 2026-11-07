"""One movement step for all active agents: seek waypoint, separate, stay walkable, queue at doors.

Positions in m, speeds in m/s, dt in s.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from sim.world import DoorState, World

AGENT_RADIUS_M = 0.25
SEPARATION_GAIN = 0.5  # fraction of the overlap removed per tick


LEVEL_OFFSET_M = 1e5  # separates levels in the KD-tree so floors never push each other


def separation(pos: np.ndarray, lv: np.ndarray) -> np.ndarray:
    """Push apart every same-level pair closer than two radii; returns a displacement per agent (m)."""
    out = np.zeros_like(pos)
    if len(pos) < 2:
        return out
    keyed = pos + np.column_stack([lv * LEVEL_OFFSET_M, np.zeros(len(lv))])
    pairs = cKDTree(keyed).query_pairs(2 * AGENT_RADIUS_M, output_type="ndarray")
    if len(pairs) == 0:
        return out
    d = pos[pairs[:, 0]] - pos[pairs[:, 1]]
    n = np.linalg.norm(d, axis=1)
    tiny = n < 1e-6
    d[tiny] = np.column_stack([np.cos(pairs[tiny, 0]), np.sin(pairs[tiny, 0])])  # deterministic split
    n[tiny] = 1e-6
    push = ((2 * AGENT_RADIUS_M - n) / 2 * SEPARATION_GAIN / n)[:, None] * d
    np.add.at(out, pairs[:, 0], push)
    np.add.at(out, pairs[:, 1], -push)
    return out


def crosses(p0: np.ndarray, p1: np.ndarray, door: DoorState) -> np.ndarray:
    """Bool per agent: the move p0->p1 crosses the door segment."""
    a, b = door.a, door.b

    def orient(p: np.ndarray, q: np.ndarray, r: np.ndarray) -> np.ndarray:
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])

    o1, o2 = orient(a, b, p0), orient(a, b, p1)
    o3, o4 = orient(p0, p1, a), orient(p0, p1, b)
    return (o1 * o2 < 0) & (o3 * o4 < 0)


HURRY_FRICTION_TOKENS = 0.05  # a hurrying person refused at a door jostles and slows the flow a bit


def _door_candidates(world: World, pos: np.ndarray, new: np.ndarray, lv: np.ndarray) -> list:
    """(door, agent rows) pairs where the move could cross the door, via a KD-tree on door midpoints."""
    tree = getattr(world, "_door_tree", None)
    if tree is None:
        mids = np.array([[(d.a[0] + d.b[0]) / 2 + d.lv * LEVEL_OFFSET_M, (d.a[1] + d.b[1]) / 2] for d in world.doors])
        half = max((float(np.linalg.norm(d.b - d.a)) / 2 for d in world.doors), default=0.0)
        tree = world._door_tree = (cKDTree(mids) if len(mids) else None, half)
    kd, half = tree
    if kd is None:
        return []
    keyed = new + np.column_stack([lv * LEVEL_OFFSET_M, np.zeros(len(lv))])
    step = float(np.max(np.linalg.norm(new - pos, axis=1))) if len(pos) else 0.0
    hits = kd.query_ball_point(keyed, half + step + 0.01)
    by_door: dict[int, list[int]] = {}
    for row, ds in enumerate(hits):
        for d in ds:
            by_door.setdefault(d, []).append(row)
    return [(world.doors[d], np.array(rows)) for d, rows in sorted(by_door.items())]


def resolve_moves(world: World, pos: np.ndarray, lv: np.ndarray, step: np.ndarray, pushable: np.ndarray,
                  hurry: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, list[tuple[int, str]]]:
    """Combine seek step and separation, keep walkable, gate door crossings by flow tokens.

    `pushable` is 0 for agents that must not move (on a stair, carried).
    Returns (new positions, denied mask, crossings as (agent row, door id)).
    """
    sep = separation(pos, lv) * pushable[:, None]
    new = pos + step + sep
    bad = ~world.nav.is_walkable(new, lv)
    if bad.any():
        new[bad] = pos[bad] + step[bad]
        bad2 = bad & ~world.nav.is_walkable(new, lv)
        new[bad2] = pos[bad2]
    denied = np.zeros(len(pos), dtype=bool)
    passed: list[tuple[int, str]] = []
    for door, rows in _door_candidates(world, pos, new, lv):
        rows = rows[crosses(pos[rows], new[rows], door)]
        if len(rows) == 0:
            continue
        mid = (door.a + door.b) / 2
        for r in rows[np.argsort(np.linalg.norm(pos[rows] - mid, axis=1), kind="stable")]:
            if door.bucket.take():
                passed.append((int(r), door.id))
            else:
                new[r] = pos[r]
                denied[r] = True
                if hurry is not None and hurry[r]:
                    door.bucket.tokens -= HURRY_FRICTION_TOKENS
    return new, denied, passed
