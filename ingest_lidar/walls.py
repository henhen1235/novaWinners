"""Wall polygons and the gaps between them (PLAN.md "walls.py", D11 superseded by D29).

Walls come from tall cells (body band occupied, points in the upper band and
a max height above 2.15 m, so 1.8-2.0 m cabinets with tops at the band edge do
not count). Grid rows follow +Z, cols +X; axis "h" walls run along X.

Orthogonal scans (wall-face direction mass within 5 deg of X/Z > 0.85) keep the
axis-aligned run detector. Other scans keep the straight runs that fit the wall
mask and trace the rest: smoothed mask contours, simplified to 3 cm, cut into
<= 1 m hole-free pieces (ids "w_t..."). Doors on traced walls come from
skeleton endpoints (skeleton_doors).
"""

from __future__ import annotations

from functools import reduce

import cv2
import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .grid import Grid, Masks, components

LEVEL = "L1"
CLOSE_CELLS = 7  # bridges gaps < 0.3 m along a wall
OPEN_CELLS = 11  # keeps runs >= ~0.5 m
ACROSS_MAX_CELLS = 20  # 1 m: anything thicker across a run is a perpendicular wall, not this one
MIN_LEN_M = 0.4
T_MIN, T_MAX = 0.10, 0.35
SNAP_M = 0.55
MARCH_M = 2.4
DOOR_MIN_M, DOOR_MAX_M = 0.6, 2.2
DOOR_FLOOR_FRAC = 0.15  # wall-face bottoms put floor-band points on wall lines; 0.3 lost doorways with floor holes
DOOR_OCC_FRAC = 0.2
TALL_MIN_H = 2.15
ORTHO_DEG = 5.0
ORTHO_SHARE = 0.85  # above: Manhattan runs only
ORTHO_MIN_EDGE_M = 0.5
THICK_CELLS = 12  # 0.6 m
TRACE_TOL_M = 0.03
PIECE_M = 1.0  # traced walls are cut on a 1 m grid: hole-free pieces, chord error <= 4 cm at r 3 m
FIT_MIN_M = 1.0
FIT_COVER = 0.8  # a straight run "fits" when the wall mask covers this share of its rectangle
FACING_COS = 0.7  # skeleton end directions within 45 deg of the line joining two jambs
SPUR_CELLS = 6  # skeleton branches shorter than 0.3 m are corner spurs, not wall ends
TRACED = "w_t"


def tall_mask(g: Grid) -> np.ndarray:
    """Cells that look like full-height wall (D9), body-only fallback on poor ceilings."""
    occ = g.body >= 2
    tall = occ & (g.upper >= 1) & (g.maxh >= min(TALL_MIN_H, g.ceiling_m - 0.15))
    return tall if tall.sum() >= 0.2 * max(1, occ.sum()) else occ


def _runs(g: Grid, tall: np.ndarray, axis: str) -> list[dict]:
    """Wall runs in one direction; axis "h" scans rows (along X), "v" the transpose."""
    t = tall if axis == "h" else tall.T
    b = (g.body if axis == "h" else g.body.T).astype(float)
    # scans see both wall faces but nothing inside: close across first (faces <= 0.3 m apart)
    m = cv2.morphologyEx(t.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((CLOSE_CELLS, 1), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((1, CLOSE_CELLS), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((1, OPEN_CELLS), np.uint8))
    # a perpendicular wall wider than OPEN_CELLS (two faces closed into one >= 0.55 m block)
    # survives the opening and would join the runs it touches into one component whose bbox
    # becomes a phantom wall across the rooms; no wall is 1 m thick, so cut those out (D26)
    m &= ~cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((ACROSS_MAX_CELLS, 1), np.uint8))
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


def _snap(walls: list[dict], fixed: list[dict] = ()) -> list[dict]:
    """Extend ends onto the far face of a perpendicular wall whose centerline is within
    SNAP_M beyond the end and whose own extent reaches this wall's line within SNAP_M.
    Closes T and L junctions with scan holes at the corner; SNAP_M < 0.6 m keeps the
    extension out of door gaps (a door gap is at least 0.6 m). `fixed` lines are snap
    targets returned unchanged."""
    out = []
    targets = list(walls) + list(fixed)
    for w in walls:
        ends = {"lo": w["lo"], "hi": w["hi"]}
        for end, sign in (("lo", -1), ("hi", 1)):
            cand = [p for p in targets if p["axis"] != w["axis"] and p["lo"] - SNAP_M <= w["c"] <= p["hi"] + SNAP_M
                    and -0.25 <= sign * (p["c"] - w[end]) <= SNAP_M]
            if cand:
                p = min(cand, key=lambda p: abs(p["c"] - w[end]))
                ends[end] = p["c"] + sign * p["t"] / 2
        out.append({**w, **ends})
    return out + list(fixed)


def wall_lines(walls: list[dict]) -> list[dict]:
    """Contract wall dicts -> {axis, c, lo, hi, t} (axis-aligned rectangles); traced walls skipped."""
    out = []
    for w in walls:
        if w["id"].startswith(TRACED):
            continue
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
    traced = [w["polygon"] for w in walls if w["id"].startswith(TRACED)]
    if traced:
        img = np.zeros(g.shape, np.uint8)
        # cell (r, c) center sits at index coords (c, r); 4 fractional bits for cv2
        pts = [np.round(((np.asarray(p) - [g.x0, g.z0]) / g.res - 0.5) * 16).astype(np.int32) for p in traced]
        cv2.fillPoly(img, pts, 1, lineType=cv2.LINE_8, shift=4)
        mask |= img > 0
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
            # a non-door piece shorter than the wall is thick reads back (wall_lines) as a wall on
            # the other axis, so the T junction it closes looks like a free end and find_gaps
            # marches across the next room; grow it into the wall it abuts (D27)
            span = gap["t"] + g.res
            pieces = [{**gap, "lo": lo, "hi": hi, "door": True}]
            if lo > gap["lo"]:
                pieces.append({**gap, "lo": min(gap["lo"], lo + g.res / 2 - span), "hi": lo + g.res / 2,
                               "door": False})
            if hi < gap["hi"]:
                pieces.append({**gap, "lo": hi - g.res / 2, "hi": max(gap["hi"], hi - g.res / 2 + span),
                               "door": False})
            return pieces
    pad = max(0.0, (gap["t"] + g.res - (gap["hi"] - gap["lo"])) / 2)
    return [{**gap, "lo": gap["lo"] - pad, "hi": gap["hi"] + pad, "door": False}]


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


def face_mask(g: Grid, tall: np.ndarray, thin: bool = True) -> np.ndarray:
    """Wall mask for tracing: both faces closed into one band (<= 0.3 m apart), blobs a
    0.6 m disc fits in removed (furniture clusters; no wall is that thick), one-face walls
    (1-2 cells, e.g. the hall's oval) thickened to 3 cells so tracing keeps them, blobs
    < 0.55 m dropped. thin=False drops 1-2 cell lines instead."""
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (CLOSE_CELLS, CLOSE_CELLS))
    m = cv2.morphologyEx(tall.astype(np.uint8), cv2.MORPH_CLOSE, disc)
    thick = cv2.morphologyEx(m, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (THICK_CELLS, THICK_CELLS)))
    m = m & (1 - thick)
    core = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    if not thin:
        m = core  # direction statistics: noisy one-cell fragments only add odd-angle edges
    else:
        m = m | cv2.dilate(m & (1 - core), np.ones((3, 3), np.uint8))
    n, lab, st = components(m, 8)
    keep = np.r_[False, np.maximum(st[1:, 2], st[1:, 3]) >= OPEN_CELLS]
    return keep[lab]


def trace(g: Grid, mask: np.ndarray):
    """Mask -> world-XZ (Multi)Polygon: marching squares on the blurred mask (sub-cell, so
    arcs and diagonals come out smooth, not as cell staircases), even-odd nesting for holes,
    Douglas-Peucker at TRACE_TOL_M."""
    from skimage.measure import find_contours

    pad = 2
    f = cv2.GaussianBlur(np.pad(mask, pad).astype(np.float32), (5, 5), 1.0)
    rings = [Polygon(c[:, ::-1]).buffer(0) for c in find_contours(f, 0.5) if len(c) >= 4]
    geom = reduce(lambda a, b: a.symmetric_difference(b), rings, Polygon())
    geom = shapely.transform(geom, lambda c: (c - pad + 0.5) * g.res + [g.x0, g.z0])
    return geom.simplify(TRACE_TOL_M)


def ortho_share(geom) -> float:
    """Length share of wall-face edges within ORTHO_DEG of the X or Z axis.

    Edges come from the traced outline re-simplified at 5 cm, counting only edges >= 0.5 m:
    scan noise roughens straight faces and blur rounds corners into short odd-angle edges,
    which pulled orthogonal bench scans down to 0.87-0.95; this way they read 0.95-0.99, the
    semicircle stress scan 0.84 and the hall scan 0.22."""
    rings = [r for p in getattr(geom.simplify(0.05), "geoms", [geom.simplify(0.05)]) if isinstance(p, Polygon)
             for r in [p.exterior, *p.interiors]]
    d = np.concatenate([np.diff(np.asarray(r.coords), axis=0) for r in rings]) if rings else np.zeros((0, 2))
    w = np.hypot(d[:, 0], d[:, 1])
    a = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 90
    w = np.where(w >= ORTHO_MIN_EDGE_M, w, 0.0)
    return float(w[np.minimum(a, 90 - a) <= ORTHO_DEG].sum() / w.sum()) if w.sum() else 1.0


def _pieces(geom) -> list[Polygon]:
    """Cut on a PIECE_M grid; holes left inside a 1 m cell are scan noise and get filled."""
    x0, z0, x1, z1 = geom.bounds
    xs = np.arange(np.floor(x0 / PIECE_M), np.ceil(x1 / PIECE_M)) * PIECE_M
    zs = np.arange(np.floor(z0 / PIECE_M), np.ceil(z1 / PIECE_M)) * PIECE_M
    cells = shapely.box(*np.meshgrid(xs, zs), *np.meshgrid(xs + PIECE_M, zs + PIECE_M))
    cells = cells.ravel()
    shapely.prepare(geom)
    cut = shapely.intersection(cells[shapely.intersects(geom, cells)], geom)
    out = []
    for part in cut:
        for p in getattr(part, "geoms", [part]):
            if isinstance(p, Polygon) and p.area >= 0.01:
                q = shapely.set_precision(Polygon(p.exterior), 0.001)
                out += [r for r in getattr(q, "geoms", [q]) if isinstance(r, Polygon) and r.area >= 0.01]
    return out


def _traced_contract(pieces: list[Polygon], height: float) -> list[dict]:
    pieces = sorted(pieces, key=lambda p: (round(p.centroid.y, 3), round(p.centroid.x, 3)))
    return [{"id": f"{TRACED}{i + 1:03d}", "level": LEVEL,
             "polygon": [[x + 0.0, z + 0.0] for x, z in list(p.exterior.coords)[:-1]],
             "height_m": round(float(height), 3)} for i, p in enumerate(pieces)]


def _bridged(g: Grid, m: Masks, lines: list[dict], height: float, traced: list[dict] = ()) -> list[dict]:
    """Manhattan walls plus bridges over their non-door gaps (traced walls are march stops)."""
    walls = _to_contract(lines, height) + list(traced)
    bridges = [q for q in find_gaps(g, m, walls) if not q["door"] and q["hi"] - q["lo"] <= DOOR_MAX_M]
    # re-snap: a bridge can be the perpendicular wall a corner end was missing. Bridges already
    # span wall to wall; snapping them could trim a short one to its neighbour's faces (D27)
    if not bridges:
        return walls
    return _to_contract(_snap(wall_lines(walls), bridges), height) + list(traced)


def detect_walls(g: Grid, m: Masks, ceiling_height_m: float) -> list[dict]:
    """Contract Wall dicts. Gaps along a wall that are not doors (< 0.6 m, wall holes
    without floor, or body-occupied gaps where only the upper band was missing) are
    bridged with extra wall rectangles so rooms do not leak through them.

    Non-orthogonal scans (D29): straight runs >= 1 m that the wall mask covers keep their
    rectangle; the rest of the mask is traced into polygon pieces."""
    tall = tall_mask(g)
    lines = _snap(_runs(g, tall, "h") + _runs(g, tall, "v"))
    if ortho_share(trace(g, face_mask(g, tall, thin=False))) > ORTHO_SHARE:
        return _bridged(g, m, lines, ceiling_height_m)
    face = face_mask(g, tall)
    geom = trace(g, face)
    keep = [w for w in lines if w["hi"] - w["lo"] >= FIT_MIN_M and face[_cells(g, w)].mean() >= FIT_COVER]
    rest = geom.difference(unary_union([Polygon(_rect(w)) for w in keep])) if keep else geom
    rest = rest.buffer(-0.05, join_style="mitre").buffer(0.05, join_style="mitre")  # side slivers of kept runs
    traced = _traced_contract([p for p in _pieces(rest) if p.area >= 0.02], ceiling_height_m)
    return _bridged(g, m, keep, ceiling_height_m, traced)


def _skeleton_branch(sk: np.ndarray, e: np.ndarray, n: int) -> np.ndarray | None:
    """Pixel n steps back from skeleton endpoint e along its branch; None if a junction
    comes first (a corner spur) or the branch is shorter."""
    prev, cur = None, tuple(e)
    for _ in range(n):
        r, c = cur
        nb = [(r + dr, c + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr or dc)
              and 0 <= r + dr < sk.shape[0] and 0 <= c + dc < sk.shape[1] and sk[r + dr, c + dc]]
        if len(nb) > (1 if prev is None else 2):
            return None
        nxt = [p for p in nb if p != prev]
        if not nxt:
            return None
        prev, cur = cur, nxt[0]
    return np.array(cur)


def skeleton_doors(g: Grid, m: Masks, walls: list[dict]) -> list[dict]:
    """Door openings at traced-wall ends, any direction: {segment [[x, z], [x, z]], width}.

    From each endpoint of the wall-mask skeleton (medial axis) on a traced wall, march
    along the branch direction across free cells to the next wall cell. A 0.6-2.2 m run
    with floor under it (>= DOOR_FLOOR_FRAC) and little occupancy (< DOOR_OCC_FRAC) is a
    door; the two jambs of one door find it twice, so centers within 0.4 m are merged."""
    tw = [w for w in walls if w["id"].startswith(TRACED)]
    if not tw:
        return []
    from skimage.morphology import skeletonize

    wm = wall_mask(g, walls)
    near = cv2.dilate(wall_mask(g, tw).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    sk = skeletonize(wm)
    nbs = cv2.filter2D(sk.astype(np.uint8), -1, np.ones((3, 3), np.float32), borderType=cv2.BORDER_CONSTANT)
    ends, dirs = [], []
    for e in np.argwhere(sk & (nbs == 2) & near):
        back = _skeleton_branch(sk, e, SPUR_CELLS)
        if back is not None:
            ends.append(e.astype(float))
            dirs.append((e - back) / np.linalg.norm(e - back))
    out: list[dict] = []
    for i, (e, d) in enumerate(zip(ends, dirs)):
        # endpoints facing each other first (a curved wall's end tangent misses the far jamb),
        # then a straight march for a jamb that faces the side of another wall
        partners = []
        for j, (f, df) in enumerate(zip(ends, dirs)):
            v = f - e
            dist = float(np.hypot(*v)) * g.res
            if j != i and DOOR_MIN_M <= dist <= DOOR_MAX_M + 0.4 and d @ v / np.hypot(*v) > FACING_COS \
                    and -(df @ v) / np.hypot(*v) > FACING_COS:
                partners.append((dist, v / np.hypot(*v)))
        for ray in [v for _, v in sorted(partners, key=lambda p: p[0])] + [d]:
            seg = _ray_gap(g, m, wm, e, ray)
            if seg is not None:
                break
        if seg is None:
            continue
        c = np.mean(seg, axis=0)
        if all(np.hypot(*(c - np.mean(o["segment"], axis=0))) > 0.4 for o in out):
            out.append({"segment": seg.tolist(), "width": float(np.hypot(*(seg[1] - seg[0])))})
    return out


def _ray_gap(g: Grid, m: Masks, wm: np.ndarray, e: np.ndarray, d: np.ndarray) -> np.ndarray | None:
    """[[x, z], [x, z]] from the wall face at e (row, col) along d to the next wall, if door-like."""
    ts = np.arange(0, (MARCH_M + 0.5) / g.res, 0.5)
    rc = np.round(e + ts[:, None] * d).astype(int)
    ok = (rc >= 0).all(1) & (rc < g.shape).all(1)
    rc, ts = rc[ok], ts[ok]
    hit = wm[rc[:, 0], rc[:, 1]]
    free = np.flatnonzero(~hit)
    if not len(free):
        return None
    t0 = free[0]
    after = np.flatnonzero(hit[t0:])
    if not len(after):
        return None
    t1 = t0 + after[0]
    width = (ts[t1] - ts[t0]) * g.res
    cells = rc[t0:t1]
    if not DOOR_MIN_M <= width <= DOOR_MAX_M or not len(cells):
        return None
    if m.occupied[cells[:, 0], cells[:, 1]].mean() >= DOOR_OCC_FRAC or \
            (g.floor[cells[:, 0], cells[:, 1]] >= 1).mean() < DOOR_FLOOR_FRAC:
        return None
    ends = e + np.array([ts[t0] - 0.25, ts[t1] - 0.25])[:, None] * d  # half a step back: the faces
    return g.cell_xz(ends[:, 0], ends[:, 1])
