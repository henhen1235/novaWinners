"""Axis-aligned wall segments and the gaps between them (PLAN.md "walls.py").

Walls are runs of tall cells (body band occupied, points in the upper band and
a max height above 2.15 m, so 1.8-2.0 m cabinets with tops at the band edge do
not count). Grid rows follow +Z, cols +X; axis "h" walls run along X.
"""

from __future__ import annotations

import cv2
import numpy as np
from shapely.geometry import Polygon

from .grid import Grid, Masks, components

LEVEL = "L1"
CLOSE_CELLS = 7  # bridges gaps < 0.3 m along a wall
OPEN_CELLS = 11  # keeps runs >= ~0.5 m
MIN_LEN_M = 0.4
T_MIN, T_MAX = 0.10, 0.35
SNAP_M = 0.55
MARCH_M = 2.4
DOOR_MIN_M, DOOR_MAX_M = 0.6, 2.2
DOOR_FLOOR_FRAC = 0.15  # wall-face bottoms put floor-band points on wall lines; 0.3 lost doorways with floor holes
DOOR_OCC_FRAC = 0.2
TALL_MIN_H = 2.15


def tall_mask(g: Grid) -> np.ndarray:
    """Cells that look like full-height wall (D9), body-only fallback on poor ceilings."""
    occ = g.body >= 2
    tall = occ & (g.upper >= 1) & (g.maxh >= TALL_MIN_H)
    return tall if tall.sum() >= 0.2 * max(1, occ.sum()) else occ


def _runs(g: Grid, tall: np.ndarray, axis: str) -> list[dict]:
    """Wall runs in one direction; axis "h" scans rows (along X), "v" the transpose."""
    t = tall if axis == "h" else tall.T
    b = (g.body if axis == "h" else g.body.T).astype(float)
    # scans see both wall faces but nothing inside: close across first (faces <= 0.3 m apart)
    m = cv2.morphologyEx(t.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((CLOSE_CELLS, 1), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((1, CLOSE_CELLS), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((1, OPEN_CELLS), np.uint8))
    n, lab, st = components(m, 8)
    o_across, o_along = (g.z0, g.x0) if axis == "h" else (g.x0, g.z0)
    out = []
    for i in range(1, n):
        x, y, w, h = st[i, :4]
        if w * g.res < MIN_LEN_M:
            continue
        wt = ((lab[y:y + h, x:x + w] == i) * b[y:y + h, x:x + w]).sum(1) + 1e-6
        rows = y + np.arange(h) + 0.5
        strong = np.flatnonzero(wt >= 0.2 * wt.max())
        thick = float(np.clip((strong[-1] - strong[0] + 1) * g.res, T_MIN, T_MAX))
        out.append({"axis": axis, "c": o_across + float((rows * wt).sum() / wt.sum()) * g.res,
                    "lo": o_along + x * g.res, "hi": o_along + (x + w) * g.res, "t": thick})
    return out


def _snap(walls: list[dict]) -> list[dict]:
    """Extend ends onto the far face of a perpendicular wall whose centerline is within
    SNAP_M beyond the end and whose own extent reaches this wall's line within SNAP_M.
    Closes T and L junctions with scan holes at the corner; SNAP_M < 0.6 m keeps the
    extension out of door gaps (a door gap is at least 0.6 m)."""
    out = []
    for w in walls:
        ends = {"lo": w["lo"], "hi": w["hi"]}
        for end, sign in (("lo", -1), ("hi", 1)):
            cand = [p for p in walls if p["axis"] != w["axis"] and p["lo"] - SNAP_M <= w["c"] <= p["hi"] + SNAP_M
                    and -0.25 <= sign * (p["c"] - w[end]) <= SNAP_M]
            if cand:
                p = min(cand, key=lambda p: abs(p["c"] - w[end]))
                ends[end] = p["c"] + sign * p["t"] / 2
        out.append({**w, **ends})
    return out


def wall_lines(walls: list[dict]) -> list[dict]:
    """Contract wall dicts -> {axis, c, lo, hi, t} (axis-aligned rectangles)."""
    out = []
    for w in walls:
        x0, z0, x1, z1 = Polygon(w["polygon"]).bounds
        if x1 - x0 >= z1 - z0:
            out.append({"axis": "h", "c": (z0 + z1) / 2, "lo": x0, "hi": x1, "t": z1 - z0})
        else:
            out.append({"axis": "v", "c": (x0 + x1) / 2, "lo": z0, "hi": z1, "t": x1 - x0})
    return out


def _cells(g: Grid, line: dict, half: float | None = None) -> tuple[slice, slice]:
    """(row slice, col slice) of a line's rectangle; `half` overrides t / 2 (meters)."""
    h = line["t"] / 2 if half is None else half
    o_across, o_along = (g.z0, g.x0) if line["axis"] == "h" else (g.x0, g.z0)
    a0 = int(round((line["c"] - h - o_across) / g.res))
    a1 = max(a0 + 1, int(round((line["c"] + h - o_across) / g.res)))
    b0 = int(round((line["lo"] - o_along) / g.res))
    b1 = max(b0 + 1, int(round((line["hi"] - o_along) / g.res)))
    across, along = slice(max(0, a0), max(0, a1)), slice(max(0, b0), max(0, b1))
    return (across, along) if line["axis"] == "h" else (along, across)


def wall_mask(g: Grid, walls: list[dict]) -> np.ndarray:
    """Rasterized wall rectangles (contract dicts) on the grid."""
    mask = np.zeros(g.shape, bool)
    for line in wall_lines(walls):
        mask[_cells(g, line)] = True
    return mask


def _march(stop: np.ndarray, g: Grid, w: dict, end: str) -> tuple[float, float] | None:
    """Gap [lo, hi] (along-axis meters) from a wall end to the next stop cell on its line."""
    s = stop if w["axis"] == "h" else stop.T
    o_across, o_along = (g.z0, g.x0) if w["axis"] == "h" else (g.x0, g.z0)
    a0 = int(round((w["c"] - w["t"] / 2 - o_across) / g.res))
    a1 = max(a0 + 1, int(round((w["c"] + w["t"] / 2 - o_across) / g.res)))
    a0, a1 = max(0, a0), min(s.shape[0], a1)
    step = 1 if end == "hi" else -1
    start = int(round((w[end] - o_along) / g.res)) - (0 if end == "hi" else 1)
    for k in range(int(round(MARCH_M / g.res)) + 1):
        col = start + step * k
        if not 0 <= col < s.shape[1] or a0 >= a1:
            return None
        if s[a0:a1, col].any():
            if k == 0:
                return None
            first, last = sorted((start, col - step))
            return o_along + (first - 0.5) * g.res, o_along + (last + 1.5) * g.res
    return None


def _at_junction(w: dict, end: str, lines: list[dict]) -> bool:
    return any(p["axis"] != w["axis"] and abs(p["c"] - w[end]) <= p["t"] / 2 + 0.1
               and p["lo"] - 0.1 <= w["c"] <= p["hi"] + 0.1 for p in lines)


def _open_run(cols_open: np.ndarray) -> tuple[int, int] | None:
    """(first, last) index of the longest run of True; None if none."""
    best, start = None, None
    for j, v in enumerate(np.r_[cols_open, False]):
        if v and start is None:
            start = j
        elif not v and start is not None:
            if best is None or j - start > best[1] - best[0] + 1:
                best = (start, j - 1)
            start = None
    return best


def _classify(g: Grid, m: Masks, gap: dict) -> list[dict]:
    """Split a gap into door / non-door pieces.

    The door candidate is the longest run of columns (across the wall strip) that
    are < 50% body-occupied, so a doorway next to a wall stretch whose upper band
    was lost in the scan is still found. Door = run 0.6-2.2 m, >= DOOR_FLOOR_FRAC of
    its strip with raw floor points (a hole in a wall has no floor under the wall
    line; build_masks may have filled it as floor_supported, so raw counts are used)
    and < DOOR_OCC_FRAC occupied. Everything else is a non-door piece.
    """
    strip = _cells(g, {**gap, "lo": gap["lo"] + g.res / 2, "hi": gap["hi"] - g.res / 2})
    ax = 0 if gap["axis"] == "h" else 1  # strip axis that runs across the wall
    occ_cols = m.occupied[strip].mean(axis=ax)
    floor_cols = (g.floor[strip] >= 1).mean(axis=ax)
    run = _open_run(occ_cols < 0.5)
    if run is not None:
        j0, j1 = run
        lo, hi = gap["lo"] + j0 * g.res, gap["lo"] + (j1 + 2) * g.res
        ok = (DOOR_MIN_M <= hi - lo <= DOOR_MAX_M and floor_cols[j0:j1 + 1].mean() >= DOOR_FLOOR_FRAC
              and occ_cols[j0:j1 + 1].mean() < DOOR_OCC_FRAC)
        if ok:
            pieces = [{**gap, "lo": lo, "hi": hi, "door": True}]
            if lo > gap["lo"]:
                pieces.append({**gap, "hi": lo + g.res / 2, "door": False})
            if hi < gap["hi"]:
                pieces.append({**gap, "lo": hi - g.res / 2, "door": False})
            return pieces
    return [{**gap, "door": False}]


def find_gaps(g: Grid, m: Masks, walls: list[dict]) -> list[dict]:
    """Gaps along wall lines, marched from free wall ends to the next wall cell
    (<= 2.4 m) on its line: {axis, c, lo, hi, t, door: bool}; deduped, sorted (see _classify)."""
    lines = wall_lines(walls)
    stop = wall_mask(g, walls)  # tall stubs too short to be walls end up in bridged pieces
    gaps: list[dict] = []
    for w in lines:
        for end in ("lo", "hi"):
            if _at_junction(w, end, lines):
                continue  # T/L junction ends are not door jambs
            span = _march(stop, g, w, end)
            if span is None:
                continue
            gap = {"axis": w["axis"], "c": w["c"], "lo": span[0], "hi": span[1], "t": w["t"]}
            mid = (gap["lo"] + gap["hi"]) / 2
            if any(o["axis"] == gap["axis"] and abs(o["c"] - gap["c"]) < 0.4
                   and abs((o["lo"] + o["hi"]) / 2 - mid) < 0.3 for o in gaps):
                continue
            gaps.append(gap)
    pieces = [p for q in gaps for p in _classify(g, m, q)]
    return sorted(pieces, key=lambda q: (round(q["c"], 3), q["axis"], round(q["lo"], 3)))


def _rect(line: dict) -> list[list[float]]:
    lo, hi, c0, c1 = line["lo"], line["hi"], line["c"] - line["t"] / 2, line["c"] + line["t"] / 2
    pts = [[lo, c0], [hi, c0], [hi, c1], [lo, c1]] if line["axis"] == "h" else \
        [[c0, lo], [c1, lo], [c1, hi], [c0, hi]]
    return [[round(float(x), 3) + 0.0, round(float(z), 3) + 0.0] for x, z in pts]


def _to_contract(lines: list[dict], height: float) -> list[dict]:
    rects = [_rect(w) for w in lines]
    rects = [r for r in rects if Polygon(r).is_valid and Polygon(r).area > 0.01]
    rects.sort(key=lambda r: (Polygon(r).centroid.y, Polygon(r).centroid.x))
    return [{"id": f"w_{i + 1:03d}", "level": LEVEL, "polygon": r, "height_m": round(float(height), 3)}
            for i, r in enumerate(rects)]


def detect_walls(g: Grid, m: Masks, ceiling_height_m: float) -> list[dict]:
    """Contract Wall dicts. Gaps along a wall that are not doors (< 0.6 m, wall holes
    without floor, or body-occupied gaps where only the upper band was missing) are
    bridged with extra wall rectangles so rooms do not leak through them."""
    tall = tall_mask(g)
    lines = _snap(_runs(g, tall, "h") + _runs(g, tall, "v"))
    walls = _to_contract(lines, ceiling_height_m)
    bridges = [q for q in find_gaps(g, m, walls) if not q["door"] and q["hi"] - q["lo"] <= DOOR_MAX_M]
    # re-snap: a bridge can be the perpendicular wall a corner end was missing
    return _to_contract(_snap(wall_lines(walls) + bridges), ceiling_height_m) if bridges else walls
