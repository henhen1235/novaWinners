"""Re-align RoomPlan rooms that were captured in separate ARSessions (different world frames).

Rooms from one session already share a frame; a room from another session sits wherever
that session's origin was, usually on top of the other rooms. RoomPlan is gravity aligned,
so the fix is a yaw + xz translation per room, found by RANSAC over door/opening pairs
(a doorway between two rooms is seen from both sides) and refined with wall directions.
Same idea as Apple's StructureBuilder, which needs shared doors/openings to merge rooms.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

MATCH_DIST_M = 0.4        # two door copies closer than this (after alignment) are one doorway
MATCH_WIDTH_M = 0.3       # and their widths differ by less than this
PARALLEL_COS = 0.95
OVERLAP_FRAC = 0.25       # floor overlap above this fraction of the smaller room = misplaced
FAR_M = 3.0               # a room this far from every placed room is treated as misplaced too
WALL_SNAP_DEG = 5.0
MIN_DOORWAYS = 2            # one shared doorway is ambiguous (wrong in 4 of 7 MyHome trials)


def rigid(theta: float, t: np.ndarray) -> np.ndarray:
    """4x4 yaw rotation (in the xz plane, about +y) plus xz translation, acting on column vectors."""
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, 0, -s, t[0]], [0, 1, 0, 0], [s, 0, c, t[1]], [0, 0, 0, 1]], float)


def _rot2(theta: float) -> np.ndarray:
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, -s], [s, c]])


def _ang(v: np.ndarray) -> float:
    return float(np.arctan2(v[1], v[0]))


def _apply(feats: list[tuple], theta: float, t: np.ndarray) -> list[tuple]:
    r = _rot2(theta)
    return [(r @ c + t, r @ d, r @ n, w) for c, d, n, w in feats]


def _matches(a: list[tuple], b: list[tuple]) -> list[tuple[int, int]]:
    """Index pairs of door features that coincide (position, direction, width)."""
    out = []
    for i, (c1, d1, _, w1) in enumerate(a):
        best = min(((float(np.hypot(*(c2 - c1))), j) for j, (c2, d2, _, w2) in enumerate(b)
                    if abs(d1 @ d2) > PARALLEL_COS and abs(w1 - w2) < MATCH_WIDTH_M), default=None)
        if best is not None and best[0] < MATCH_DIST_M:
            out.append((i, best[1]))
    return out


def _overlap(poly: Polygon, others: Polygon) -> float:
    if poly.is_empty or others.is_empty:
        return 0.0
    return poly.intersection(others).area / max(min(poly.area, others.area), 1e-6)


def _move(poly: Polygon, theta: float, t: np.ndarray) -> Polygon:
    r = _rot2(theta)
    return Polygon([r @ p + t for p in np.asarray(poly.exterior.coords)])


def _wall_snap(walls: list[tuple], placed_walls: list[tuple]) -> float:
    """Yaw correction (rad) that makes nearby walls parallel: length-weighted median residual mod 90 deg."""
    res, wts = [], []
    for c, d, _, length in walls:
        for c2, d2, _, _ in placed_walls:
            if np.hypot(*(c2 - c)) > length / 2 + 1.0:
                continue
            r = (np.degrees(_ang(d2) - _ang(d)) + 45.0) % 90.0 - 45.0
            if abs(r) < WALL_SNAP_DEG:
                res.append(r)
                wts.append(length)
    if not res:
        return 0.0
    order = np.argsort(res)
    cum = np.cumsum(np.asarray(wts)[order])
    return float(np.radians(np.asarray(res)[order][np.searchsorted(cum, cum[-1] / 2)]))


def _shared_walls(walls: list[tuple], placed_walls: list[tuple]) -> list[tuple[np.ndarray, float, float]]:
    """Wall pairs that look like the two faces of one wall: anti-parallel, overlapping, 2-50 cm apart.

    Returns (placed normal, gap, overlap length) per pair; the gap is measured behind the placed face.
    """
    out = []
    for c, d, n, length in walls:
        for c2, d2, n2, l2 in placed_walls:
            if n @ n2 > -PARALLEL_COS:
                continue
            gap = float((c - c2) @ -n2)
            s = float((c - c2) @ d2)
            overlap = min(s + length / 2, l2 / 2) - max(s - length / 2, -l2 / 2)
            if 0.02 < gap < 0.5 and overlap > 0.3:
                out.append((n2, gap, overlap))
    return out


def wall_thickness(units: list[dict]) -> float:
    """Median gap between the two faces of walls shared by rooms (neonplan3d's closeGaps idea), default 12 cm."""
    gaps = [g for i, u in enumerate(units) for j, v in enumerate(units) if i < j
            for _, g, _ in _shared_walls(u["walls"], v["walls"])]
    return float(np.median(gaps)) if len(gaps) >= 3 else 0.12


def _fit(pairs: list[tuple[tuple, tuple]]) -> tuple[float, np.ndarray]:
    """Least-squares yaw + translation mapping door features onto their matches (2D Kabsch)."""
    src = np.array([p[0][0] for p in pairs])
    dst = np.array([p[1][0] for p in pairs])
    sm, dm = src.mean(0), dst.mean(0)
    h = (src - sm).T @ (dst - dm)
    theta = float(np.arctan2(h[0, 1] - h[1, 0], h[0, 0] + h[1, 1]))
    return theta, dm - _rot2(theta) @ sm


def align(units: list[dict]) -> tuple[list[tuple[float, np.ndarray] | None], list[str]]:
    """Per room: None (frame kept) or (yaw, xz translation) that moves it next to the other rooms.

    `units` items: {"poly": floor Polygon, "doors": [(c, d, n, width)], "walls": [(c, d, n, length)]}.
    """
    n = len(units)
    if n < 2:
        return [None] * n, []
    area = [u["poly"].area for u in units]
    links = [sum(bool(_matches(units[i]["doors"], units[j]["doors"])) for j in range(n) if j != i) for i in range(n)]
    anchor = max(range(n), key=lambda i: (links[i], area[i]))
    placed, result, notes = {anchor}, [None] * n, []

    def placed_union() -> Polygon:
        return unary_union([units[i]["poly"] for i in placed])

    def fits(i: int) -> bool:
        return _overlap(units[i]["poly"], placed_union()) <= OVERLAP_FRAC and \
            units[i]["poly"].distance(placed_union()) < FAR_M

    # Trust the capture frames first: rooms that share a doorway with placed rooms, then any room
    # that neither overlaps nor floats far away. Only the rest are searched for and moved.
    for need_door in (True, False):
        changed = True
        while changed:
            changed = False
            for i in sorted(set(range(n)) - placed, key=lambda i: (-links[i], -area[i])):
                others = [f for j in placed for f in units[j]["doors"]]
                if fits(i) and (not need_door or _matches(units[i]["doors"], others)):
                    placed.add(i)
                    changed = True
    thickness = wall_thickness([units[j] for j in placed])
    for i in sorted(set(range(n)) - placed, key=lambda i: -area[i]):
        best = _search(units[i], [units[j] for j in placed], placed_union(), thickness)
        if best is None or best[2] < MIN_DOORWAYS:
            notes.append(f"room {i} overlaps or lies far from the other rooms (separate capture session?) "
                         f"and shares {0 if best is None else best[2]} doorway(s) with them; frame kept, check it")
            continue
        result[i] = (best[0], best[1])
        units[i] = {**units[i], "poly": _move(units[i]["poly"], best[0], best[1]),
                    "doors": _apply(units[i]["doors"], best[0], best[1]),
                    "walls": _apply_walls(units[i]["walls"], best[0], best[1])}
        placed.add(i)
        notes.append(f"room {i}: re-aligned by {np.degrees(best[0]):.1f} deg, "
                     f"[{best[1][0]:.2f}, {best[1][1]:.2f}] m from {best[2]} shared doorway(s)")
    return result, notes


def _apply_walls(walls: list[tuple], theta: float, t: np.ndarray) -> list[tuple]:
    r = _rot2(theta)
    return [(r @ c + t, r @ d, r @ n, length) for c, d, n, length in walls]


def _search(unit: dict, placed: list[dict], placed_poly: Polygon,
            thickness: float) -> tuple[float, np.ndarray, int] | None:
    """RANSAC over (this door, placed door) pairs; returns (yaw, t, inlier doors) or None."""
    targets = [f for p in placed for f in p["doors"]]
    placed_walls = [w for p in placed for w in p["walls"]]
    best, best_key = None, None
    for c, d, nrm, w in unit["doors"]:
        for c2, d2, n2, w2 in targets:
            if abs(w - w2) > MATCH_WIDTH_M:
                continue
            for flip in (False, True):  # doorway seen from the other side, or the same side
                theta = _ang(n2 if flip else -n2) - _ang(nrm)
                t = c2 - _rot2(theta) @ c
                moved = _apply(unit["doors"], theta, t)
                pairs = [(unit["doors"][i], targets[j]) for i, j in _matches(moved, targets)]
                if not pairs:
                    continue
                poly = _move(unit["poly"], theta, t)
                ov = _overlap(poly, placed_poly)
                if ov > OVERLAP_FRAC:
                    continue
                support = sum(o for _, _, o in _shared_walls(_apply_walls(unit["walls"], theta, t), placed_walls))
                key = (len(pairs) + min(support, 6.0) / 2.0 - 4.0 * ov, -ov)
                if best_key is None or key > best_key:
                    best_key, best = key, (theta, t, pairs)
    if best is None:
        return None
    theta, t, pairs = best
    if len(pairs) > 1:
        theta, t = _fit(pairs)
    # Snap yaw to the walls of the placed rooms, then re-fit the translation to the doorways.
    theta += _wall_snap(_apply_walls(unit["walls"], theta, t), placed_walls)
    t = np.mean([q[0] - _rot2(theta) @ p[0] for p, q in pairs], axis=0)
    # Translation: doorways (loose, copies disagree by ~10 cm) plus shared walls held at the
    # measured wall thickness (tight), solved together by weighted least squares.
    shared = _shared_walls(_apply_walls(unit["walls"], theta, t), placed_walls)
    rows = [np.eye(2) / 0.15 for _ in pairs] + [(-n2[None, :]) / 0.03 for n2, _, _ in shared]
    rhs = [(q[0] - (_rot2(theta) @ p[0] + t)) / 0.15 for p, q in pairs] + \
        [np.array([thickness - g]) / 0.03 for _, g, _ in shared]
    delta = np.linalg.lstsq(np.vstack(rows), np.concatenate(rhs), rcond=None)[0]
    return theta, t + delta, len(pairs)
