"""Rooms as connected interior space between walls and door segments (D7), with a
Hydra-style distance-transform split for big rooms whose door was missed."""

from __future__ import annotations

import heapq

import cv2
import numpy as np
import shapely
from shapely.geometry import Polygon

from .geometry import ring
from .grid import AGENT_RADIUS_M, Grid, Masks, components, vectorize
from .walls import DOOR_FLOOR_FRAC, DOOR_MAX_M, DOOR_MIN_M, find_gaps, skeleton_doors, wall_mask

LEVEL = "L1"
MIN_ROOM_M2 = 1.0
SPLIT_MIN_M2 = 8.0
SPLIT_DELTAS = np.arange(0.30, 0.551, 0.05)
SEED_MIN_M2 = 0.3
PROBE_M = 0.3


def _door_strip(g: Grid, gap: dict) -> tuple[slice, slice]:
    """Cells of a 2-cell-thick strip on the gap centerline (door barrier)."""
    o_across, o_along = (g.z0, g.x0) if gap["axis"] == "h" else (g.x0, g.z0)
    a = int(round((gap["c"] - o_across) / g.res))
    b0, b1 = int(round((gap["lo"] - o_along) / g.res)), int(round((gap["hi"] - o_along) / g.res))
    across, along = slice(max(0, a - 1), a + 1), slice(max(0, b0), max(0, b1))
    return (across, along) if gap["axis"] == "h" else (along, across)


def _merge_small(lab: np.ndarray, n: int, min_cells: int) -> np.ndarray:
    """Labels < min_cells merge into the neighbor with the most adjacent cells, else 0."""
    sizes = np.bincount(lab.ravel(), minlength=n)
    out = lab.copy()
    k = np.ones((5, 5), np.uint8)
    for i in range(1, n):
        if sizes[i] >= min_cells or sizes[i] == 0:
            continue
        cur = out == i
        ring_ = (cv2.dilate(cur.astype(np.uint8), k) > 0) & ~cur
        nb = out[ring_]
        nb = nb[(nb > 0) & (sizes[nb] >= min_cells)]
        out[cur] = np.bincount(nb).argmax() if len(nb) else 0
    return out


def _boundary_segment(g: Grid, cells: np.ndarray) -> tuple[list[list[float]], float]:
    """Segment along the long axis of boundary cells (rows, cols) and its length (m)."""
    xz = g.cell_xz(cells[:, 0], cells[:, 1])
    span = xz.max(0) - xz.min(0) + g.res
    along = int(np.argmax(span))
    c = np.median(xz, axis=0)
    a, b = c.copy(), c.copy()
    a[along], b[along] = xz[:, along].min() - g.res / 2, xz[:, along].max() + g.res / 2
    return [a.tolist(), b.tolist()], float(span[along])


def _flood(elev: np.ndarray, markers: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Priority-flood watershed (Meyer) of `markers` over `mask`, 4-connected.

    scipy's watershed_ift mislabels plateaus here, so this is done by hand;
    ponytail: Python heap, ~0.2 s per 50k cells, use skimage if it gets slow.
    """
    lab = markers.copy()
    heap = [(float(elev[r, c]), int(r), int(c)) for r, c in zip(*np.nonzero(lab))]
    heapq.heapify(heap)
    nr, nc = lab.shape
    while heap:
        _, r, c = heapq.heappop(heap)
        for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= rr < nr and 0 <= cc < nc and mask[rr, cc] and not lab[rr, cc]:
                lab[rr, cc] = lab[r, c]
                heapq.heappush(heap, (float(elev[rr, cc]), rr, cc))
    return lab


def _split(g: Grid, comp: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int, list, float]]]:
    """Watershed split of one component; returns (labels 1..k inside comp, doors (a, b, seg, w)).

    Count seeds (D > delta, >= 0.3 m^2) for each delta; n = upper median count.
    Boundaries 0.6-2.2 m long become doors, other neighbors are merged back.
    """
    dist = cv2.distanceTransform(np.pad(comp, 1).astype(np.uint8), cv2.DIST_L2, 5)[1:-1, 1:-1] * g.res
    min_cells = SEED_MIN_M2 / g.res**2
    seeds = []
    for d in SPLIT_DELTAS:
        nn, lab, st = components(dist > d, 8)
        keep = [i for i in range(1, nn) if st[i, 4] >= min_cells]
        seeds.append((len(keep), lab, keep))
    counts = sorted(s[0] for s in seeds)
    n = counts[len(counts) // 2]
    if n < 2:
        return comp.astype(np.int32), []
    _, lab, keep = next(s for s in seeds if s[0] == n)
    markers = np.zeros(comp.shape, np.int32)
    for j, i in enumerate(keep):
        markers[lab == i] = j + 1
    parts = _flood(-dist, markers, comp)
    # union-find merge of parts whose shared boundary is not door-like
    parent = list(range(n + 1))

    def find(x: int) -> int:
        while parent[x] != x:
            x = parent[x]
        return x

    pairs = {}
    for dr, dc in ((0, 1), (1, 0)):
        a = parts[: parts.shape[0] - dr, : parts.shape[1] - dc]
        b = parts[dr:, dc:]
        hit = (a > 0) & (b > 0) & (a != b)
        rr, cc = np.nonzero(hit)
        for r, c, p, q in zip(rr, cc, a[hit], b[hit]):
            pairs.setdefault((min(p, q), max(p, q)), []).append((r, c))
    doors = []
    for (p, q), cells in sorted(pairs.items()):
        seg, width = _boundary_segment(g, np.array(cells))
        if DOOR_MIN_M <= width <= DOOR_MAX_M:
            doors.append((p, q, seg, width))
        else:
            parent[find(q)] = find(p)
    roots = np.array([find(i) for i in range(n + 1)])
    merged = roots[parts]
    merged[parts == 0] = 0
    doors = [(int(roots[p]), int(roots[q]), s, w) for p, q, s, w in doors if roots[p] != roots[q]]
    return merged, doors


def _room_type(poly: Polygon) -> str:
    rect = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:3]
    a, b = sorted((np.linalg.norm(rect[1] - rect[0]), np.linalg.norm(rect[2] - rect[1])))
    return "corridor" if a > 0 and b / a >= 3 and a <= 2.5 else "unknown"


def _label_at(lab: np.ndarray, rc: np.ndarray) -> int:
    """Most common non-zero label within 2 cells of (row, col); 0 if none."""
    r, c = rc
    win = lab[max(0, r - 2): r + 3, max(0, c - 2): c + 3]
    v = win[win > 0]
    return int(np.bincount(v).argmax()) if len(v) else 0


def _round_seg(seg: list) -> list[list[float]]:
    return [[round(float(x), 3) + 0.0, round(float(z), 3) + 0.0] for x, z in seg]


def segment_rooms(g: Grid, m: Masks, walls: list[dict]) -> tuple[list[dict], list[dict]]:
    """(rooms, interior doors) as contract dicts; exit doors are added by the pipeline."""
    wm = wall_mask(g, walls)
    pieces = find_gaps(g, m, walls)
    gaps = [q for q in pieces if q["door"]]
    barrier = wm.copy()
    # non-door pieces too: detect_walls bridged the first pass's pieces, but re-snapped walls
    # shift the gaps, and an unbridged sliver next to a door strip leaks one room into the next (D25)
    for q in pieces:
        if q["door"] or q["hi"] - q["lo"] <= DOOR_MAX_M:
            barrier[_door_strip(g, q)] = True
    # doors on traced (non-axis) walls; the axis-aligned gap logic owns the rest (dedupe 0.5 m)
    centers = [np.array([(q["lo"] + q["hi"]) / 2, q["c"]] if q["axis"] == "h" else [q["c"], (q["lo"] + q["hi"]) / 2])
               for q in gaps]
    sk_doors = [d for d in skeleton_doors(g, m, walls)
                if all(np.hypot(*(np.mean(d["segment"], axis=0) - c)) > 0.5 for c in centers)]
    if sk_doors:
        img = np.zeros(g.shape, np.uint8)
        for d in sk_doors:
            p0, p1 = np.asarray(d["segment"], float)
            u = (p1 - p0) / np.linalg.norm(p1 - p0) * g.res  # one cell into each jamb seals the strip
            (r0, c0), (r1, c1) = g.rc(np.array([p0 - u, p1 + u]))
            cv2.line(img, (int(c0), int(r0)), (int(c1), int(r1)), 1, 2)
        barrier |= img > 0
    near_wall = cv2.dilate(wm.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    interior = ((m.free | (m.occupied & ~near_wall)) & ~wm) | m.floor_supported
    interior &= ~barrier
    n, lab, _ = components(interior, 4)
    lab = _merge_small(lab, n, int(MIN_ROOM_M2 / g.res**2))

    # Hydra-style split of big components that probably hide a missed door
    final = np.zeros_like(lab)
    split_doors = []
    nxt = 1
    for i in range(1, n):
        comp = lab == i
        cells = int(comp.sum())
        if not cells:
            continue
        if cells * g.res**2 > SPLIT_MIN_M2:
            parts, dd = _split(g, comp)
        else:
            parts, dd = comp.astype(np.int32), []
        ids = {}
        for p in np.unique(parts[parts > 0]):
            ids[int(p)] = nxt
            final[parts == p] = nxt
            nxt += 1
        split_doors += [(ids[p], ids[q], s, w) for p, q, s, w in dd]

    rooms, by_label = [], {}
    for k in range(1, nxt):
        mp = vectorize(final == k, g)
        if mp.is_empty:
            continue
        poly = Polygon(max(mp.geoms, key=lambda p: p.area).exterior)
        if poly.area < MIN_ROOM_M2 or not poly.is_valid:
            continue
        by_label[k] = poly
    order = sorted(by_label, key=lambda k: (round(by_label[k].centroid.x, 3), round(by_label[k].centroid.y, 3)))
    counters = {"corridor": 0, "unknown": 0}
    rid = {}
    for k in order:
        poly = by_label[k]
        rtype = _room_type(poly)
        counters[rtype] += 1
        name = f"{'Corridor' if rtype == 'corridor' else 'Room'} {counters[rtype]}"
        rid[k] = f"r_{len(rooms) + 1:03d}"
        rooms.append({"id": rid[k], "level": LEVEL, "name": name, "type": rtype, "polygon": ring(poly),
                      "area_m2": round(poly.area, 3),
                      "centroid": [round(poly.centroid.x, 3) + 0.0, round(poly.centroid.y, 3) + 0.0],
                      "has_window": False})

    doors = []
    for q in gaps:
        normal = np.array([0.0, 1.0]) if q["axis"] == "h" else np.array([1.0, 0.0])
        mid = (q["lo"] + q["hi"]) / 2
        center = np.array([mid, q["c"]]) if q["axis"] == "h" else np.array([q["c"], mid])
        off = normal * (q["t"] / 2 + PROBE_M)
        a, b = (_label_at(final, g.rc(center + s * off)[0]) for s in (-1, 1))
        if a in rid and b in rid and a != b:
            seg = [[q["lo"], q["c"]], [q["hi"], q["c"]]] if q["axis"] == "h" else \
                [[q["c"], q["lo"]], [q["c"], q["hi"]]]
            doors.append((rid[a], rid[b], seg, q["hi"] - q["lo"]))
    for d in sk_doors:
        p0, p1 = np.asarray(d["segment"], float)
        normal = np.array([p0[1] - p1[1], p1[0] - p0[0]]) / np.linalg.norm(p1 - p0)
        a, b = (_label_at(final, g.rc((p0 + p1) / 2 + s * normal * (0.075 + PROBE_M))[0]) for s in (-1, 1))
        if a in rid and b in rid and a != b:
            doors.append((rid[a], rid[b], d["segment"], d["width"]))
    doors += [(rid[p], rid[q], s, w) for p, q, s, w in split_doors if p in rid and q in rid]
    doors.sort(key=lambda d: (round(float(np.mean(d[2], axis=0)[0]), 3), round(float(np.mean(d[2], axis=0)[1]), 3)))
    out = [{"id": f"d_{i + 1:03d}", "level": LEVEL, "segment": _round_seg(seg),
            "width_m": round(float(np.clip(w, DOOR_MIN_M, DOOR_MAX_M)), 3), "connects": [a, b],
            "is_exit": False, "kind": "opening", "fire_rated": None, "opens_toward": None}
           for i, (a, b, seg, w) in enumerate(doors)]
    return rooms, out


def _room_labels(g: Grid, rooms: list[dict]) -> np.ndarray:
    """Grid of 1-based room indices (cell centers inside the room polygon), 0 elsewhere."""
    lab = np.zeros(g.shape, np.int32)
    for i, r in enumerate(rooms, 1):
        x0, z0, x1, z1 = Polygon(r["polygon"]).bounds
        (r0, c0), (r1, c1) = g.rc([[x0, z0], [x1, z1]])
        r0, c0 = max(0, r0), max(0, c0)
        rr, cc = np.mgrid[r0:r1 + 1, c0:c1 + 1]
        rr, cc = rr.ravel(), cc.ravel()
        ok = (rr < g.shape[0]) & (cc < g.shape[1])
        rr, cc = rr[ok], cc[ok]
        xz = g.cell_xz(rr, cc)
        inside = shapely.contains_xy(Polygon(r["polygon"]), xz[:, 0], xz[:, 1])
        lab[rr[inside], cc[inside]] = i
    return lab


def connect_unreachable(g: Grid, m: Masks, rooms: list[dict], doors: list[dict], start: int) -> list[dict]:
    """Opening doors for rooms with no door path to an exit whose walkable area reaches a
    reachable room's walkable area across a barrier (a missed door, D28). Walkable cells
    outside every room polygon are the crossings; each room reached first through a crossing
    (floor-backed, then narrowest first) gets one door on that crossing's long axis,
    width = crossing + agent diameter. `start` numbers the new door ids."""
    adj: dict[str, set[str]] = {r["id"]: set() for r in rooms}
    adj["outside"] = set()
    for d in doors:
        a, b = d["connects"]
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    reach: set[str] = set()

    def spread(src: str) -> None:
        reach.add(src)
        todo = [src]
        while todo:
            for nb in sorted(adj[todo.pop()] - reach):
                reach.add(nb)
                todo.append(nb)

    spread("outside")
    if all(r["id"] in reach for r in rooms):
        return []
    lab = _room_labels(g, rooms)
    n, cross = cv2.connectedComponents((m.walkable & (lab == 0)).astype(np.uint8), connectivity=8)
    edges = []  # (no floor seen, crossing length m, room a, room b, segment)
    for k in range(1, n):
        cells = np.argwhere(cross == k)
        # a wall hole filled as floor (D10) has no floor points; big scans are sampled too thin
        # to require them (0.5 points per floor cell), so they only rank first
        holey = (g.floor[cells[:, 0], cells[:, 1]] >= 1).mean() < DOOR_FLOOR_FRAC
        ring_ = cv2.dilate((cross == k).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        touched = sorted(set(lab[ring_ & m.walkable].tolist()) - {0})
        if len(touched) < 2:
            continue
        seg, length = _boundary_segment(g, cells)
        edges += [(holey, round(length, 3), rooms[a - 1]["id"], rooms[b - 1]["id"], seg)
                  for i, a in enumerate(touched) for b in touched[i + 1:]]
    edges.sort(key=lambda e: e[:4])
    out = []
    while True:  # narrowest floor-backed crossing out of the reachable set first
        e = next((e for e in edges if (e[2] in reach) != (e[3] in reach)), None)
        if e is None:
            return out
        _, length, a, b, seg = e
        a, b = (a, b) if a in reach else (b, a)
        out.append({"id": f"d_{start + len(out):03d}", "level": LEVEL, "segment": _round_seg(seg),
                    "width_m": round(float(np.clip(length + 2 * AGENT_RADIUS_M, DOOR_MIN_M, DOOR_MAX_M)), 3),
                    "connects": [a, b], "is_exit": False, "kind": "opening", "fire_rated": None,
                    "opens_toward": None})
        spread(b)
