"""One movement step for all active agents: seek waypoint, separate, stay walkable, queue at doors.

Positions in m, speeds in m/s, dt in s.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from sim.world import DoorState, World

AGENT_RADIUS_M = 0.25
SEPARATION_GAIN = 0.5  # fraction of the overlap removed per tick


def separation(pos: np.ndarray) -> np.ndarray:
    """Push apart every pair closer than two radii; returns a displacement per agent (m)."""
    out = np.zeros_like(pos)
    if len(pos) < 2:
        return out
    pairs = cKDTree(pos).query_pairs(2 * AGENT_RADIUS_M, output_type="ndarray")
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


def resolve_moves(world: World, pos: np.ndarray, step: np.ndarray, pushable: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[tuple[int, str]]]:
    """Combine seek step and separation, keep walkable, gate door crossings by flow tokens.

    Returns (new positions, denied mask, crossings as (agent row, door id)).
    """
    sep = separation(pos) * pushable[:, None]
    new = pos + step + sep
    bad = ~world.nav.is_walkable(new)
    if bad.any():
        new[bad] = pos[bad] + step[bad]
        bad2 = bad & ~world.nav.is_walkable(new)
        new[bad2] = pos[bad2]
    denied = np.zeros(len(pos), dtype=bool)
    passed: list[tuple[int, str]] = []
    for door in world.doors:
        rows = np.flatnonzero(crosses(pos, new, door))
        if len(rows) == 0:
            continue
        mid = (door.a + door.b) / 2
        for r in rows[np.argsort(np.linalg.norm(pos[rows] - mid, axis=1), kind="stable")]:
            if door.bucket.take():
                passed.append((int(r), door.id))
            else:
                new[r] = pos[r]
                denied[r] = True
    return new, denied, passed
