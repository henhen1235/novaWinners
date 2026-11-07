"""Occupancy grid, masks, walkable area and exit openings.

Frame: scene frame (meters, Y up, floor near y = 0). Grid cells are RES meters;
row index follows +Z, column index follows +X, so a PNG of any mask is a
top-down view with +X right and +Z down (same as the contract's image rule).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from .load import Scan
from .orient import apply

RES = 0.05
FLOOR_BAND = (-0.10, 0.10)
BODY_BAND = (0.20, 1.80)
UPPER_BAND = (2.00, 2.40)
TILE_CELLS = 40  # 2 m tiles for the local floor estimate
AGENT_RADIUS_M = 0.25


@dataclass(frozen=True)
class Grid:
    """Per-cell point counts in height bands relative to the local floor."""

    x0: float
    z0: float
    res: float
    floor: np.ndarray  # int32 (nz, nx)
    body: np.ndarray
    upper: np.ndarray
    maxh: np.ndarray  # float32, max height above local floor below 2.6 m (0 if none)
    noise_sigma: float = 0.0  # robust std of floor heights (m): scan noise estimate
    ceiling_m: float = 3.0  # floor-to-ceiling height the bands were scaled to (D20)

    @property
    def shape(self) -> tuple[int, int]:
        return self.floor.shape

    def cell_xz(self, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        """World [x, z] of cell centers."""
        return np.c_[self.x0 + (np.asarray(cols) + 0.5) * self.res, self.z0 + (np.asarray(rows) + 0.5) * self.res]

    def rc(self, xz: np.ndarray) -> np.ndarray:
        """(row, col) integer cell indices of world [x, z] points."""
        xz = np.atleast_2d(np.asarray(xz, dtype=float))
        return np.c_[np.floor((xz[:, 1] - self.z0) / self.res), np.floor((xz[:, 0] - self.x0) / self.res)].astype(int)


def sample_points(scan: Scan, matrix: np.ndarray, seed: int = 0) -> np.ndarray:
    """Area-weighted surface samples in the scene frame, (n, 3) meters, deterministic."""
    pts = apply(matrix, scan.vertices)
    if scan.faces is None:
        return pts
    rng = np.random.default_rng(seed)
    tri = pts[scan.faces]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    n = int(np.clip(4000 * area.sum(), 2_000_000, 6_000_000))
    idx = rng.choice(len(area), n, p=area / area.sum())
    u, v = rng.random(n), rng.random(n)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    t = tri[idx]
    return t[:, 0] + u[:, None] * (t[:, 1] - t[:, 0]) + v[:, None] * (t[:, 2] - t[:, 0])


def _local_floor(pts: np.ndarray, rows: np.ndarray, cols: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Floor height per cell from the mode of low points in 2 m tiles (handles drift)."""
    nt = (shape[0] // TILE_CELLS + 1, shape[1] // TILE_CELLS + 1)
    low = np.abs(pts[:, 1]) < 0.25
    tile = (rows[low] // TILE_CELLS) * nt[1] + cols[low] // TILE_CELLS
    ybin = np.clip(((pts[low, 1] + 0.25) / 0.01).astype(int), 0, 49)
    hist = np.bincount(tile * 50 + ybin, minlength=nt[0] * nt[1] * 50).reshape(-1, 50)
    hist = cv2.GaussianBlur(hist.astype(np.float32), (5, 1), 0)
    tiles = np.where(hist.sum(1) >= 30, hist.argmax(1) * 0.01 - 0.245, np.nan).reshape(nt)
    fill = float(np.nanmedian(tiles)) if np.isfinite(tiles).any() else 0.0
    tiles = np.where(np.isnan(tiles), fill, tiles).astype(np.float32)
    up = cv2.resize(tiles, (nt[1] * TILE_CELLS, nt[0] * TILE_CELLS), interpolation=cv2.INTER_LINEAR)
    return up[: shape[0], : shape[1]]


def upper_band(ceiling_m: float) -> tuple[float, float]:
    """UPPER_BAND for a 3 m ceiling; lower ceilings shift it down so the ceiling slab
    itself (at ceiling_m +- noise) never lands in it (D20)."""
    return min(UPPER_BAND[0], ceiling_m - 0.55), min(UPPER_BAND[1], ceiling_m - 0.12)


def build_grid(pts: np.ndarray, res: float = RES, ceiling_m: float = 3.0) -> Grid:
    """Rasterize scene-frame points; points outside plausible heights are ignored."""
    p = pts[(pts[:, 1] > -0.5) & (pts[:, 1] < 3.0)]
    lo = np.floor(p[:, [0, 2]].min(0) / res) * res - 0.5
    hi = p[:, [0, 2]].max(0) + 0.5
    nx, nz = int((hi[0] - lo[0]) / res) + 1, int((hi[1] - lo[1]) / res) + 1
    cols = ((p[:, 0] - lo[0]) / res).astype(int)
    rows = ((p[:, 2] - lo[1]) / res).astype(int)
    h = p[:, 1] - _local_floor(p, rows, cols, (nz, nx))[rows, cols]
    flat = rows * nx + cols

    def band(b: tuple[float, float]) -> np.ndarray:
        m = (h >= b[0]) & (h <= b[1])
        return np.bincount(flat[m], minlength=nz * nx).reshape(nz, nx).astype(np.int32)

    maxh = np.zeros(nz * nx, np.float32)
    m = (h > 0.05) & (h < min(2.6, ceiling_m - 0.1))  # below the ceiling slab
    np.maximum.at(maxh, flat[m], h[m].astype(np.float32))
    fh = h[np.abs(h) < 0.1]
    sigma = float(1.4826 * np.median(np.abs(fh - np.median(fh)))) if len(fh) > 100 else 0.0
    return Grid(float(lo[0]), float(lo[1]), res, band(FLOOR_BAND), band(BODY_BAND), band(upper_band(ceiling_m)),
                maxh.reshape(nz, nx), sigma, ceiling_m)


def components(mask: np.ndarray, conn: int = 4) -> tuple[int, np.ndarray, np.ndarray]:
    """cv2 connected components of a boolean mask: (count incl. background 0, labels, stats)."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=conn)
    return n, lab, stats


def _border_ids(lab: np.ndarray) -> set[int]:
    return set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]).tolist()) - {0}


@dataclass(frozen=True)
class Masks:
    """Boolean masks on the grid. free = floor_supported & ~occupied (before erosion)."""

    floor_supported: np.ndarray
    occupied: np.ndarray
    unknown: np.ndarray
    outside: np.ndarray  # unknown connected to the grid border: never walkable
    free: np.ndarray
    walkable: np.ndarray  # cells whose center is > agent radius from any non-free cell


def build_masks(g: Grid, radius_m: float = AGENT_RADIUS_M, max_fill_m2: float = 1.0) -> Masks:
    """Derive masks; see DECISIONS.md D10 for the hole-filling rule."""
    cell = g.res * g.res
    occupied = g.body >= 2
    floor = cv2.morphologyEx((g.floor >= 1).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0
    floor &= ~occupied
    # furniture interiors: small enclosed non-occupied pockets with almost no floor seen
    n, lab, st = components(~occupied)
    border = _border_ids(lab)
    fl = np.bincount(lab.ravel(), weights=floor.ravel(), minlength=n)
    pocket = np.array([i > 0 and i not in border and st[i, 4] * cell < 3.0 and fl[i] < 0.2 * st[i, 4]
                       for i in range(n)])
    occupied = occupied | pocket[lab]
    floor &= ~occupied
    unknown = ~floor & ~occupied
    n, lab, st = components(unknown)
    border = _border_ids(lab)
    is_out = np.array([i in border for i in range(n)])
    small = np.array([i > 0 and i not in border and st[i, 4] * cell <= max_fill_m2 for i in range(n)])
    floor |= small[lab]
    outside = is_out[lab]
    unknown = ~floor & ~occupied
    free = floor & ~occupied
    n, lab, st = components(free)
    keep = st[:, 4] * cell >= 0.5
    keep[0] = False
    free = keep[lab]
    dist = cv2.distanceTransform(free.astype(np.uint8), cv2.DIST_L2, 5) * g.res
    # cell-center distance: a surface lies on average half a cell inside its
    # occupied cell, so center-to-center distance is the unbiased estimate.
    # Noise spills points about one sigma past each surface; give that back.
    walkable = dist > radius_m - min(g.noise_sigma, 0.03)
    return Masks(floor, occupied, unknown, outside, free, walkable)


def vectorize(mask: np.ndarray, g: Grid, simplify_m: float = 0.03) -> MultiPolygon:
    """Mask -> MultiPolygon in world XZ, boundary on cell edges, simplified."""
    cs, hier = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return MultiPolygon()
    hier = hier[0]

    def world(c: np.ndarray) -> np.ndarray:
        c = c[:, 0, :]
        return g.cell_xz(c[:, 1], c[:, 0])

    polys = []
    for i, c in enumerate(cs):
        if hier[i][3] != -1 or len(c) < 3:
            continue
        holes, j = [], hier[i][2]
        while j != -1:
            if len(cs[j]) >= 3:
                holes.append(world(cs[j]))
            j = hier[j][0]
        polys.append(Polygon(world(c), holes).buffer(0))
    geom = unary_union(polys).buffer(g.res / 2, join_style="mitre").simplify(simplify_m).buffer(0)
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return MultiPolygon([p for p in parts if isinstance(p, Polygon) and p.area > 0.01])


def exit_openings(g: Grid, m: Masks, min_w: float = 0.6, max_w: float = 3.0) -> tuple[list[dict], list[dict]]:
    """Free floor meeting outside-connected unknown with no wall in between.

    Returns (openings, wide); each item {"segment": [[x, z], [x, z]], "width_m", "center"}.
    Openings wider than max_w go to `wide` (LEAKY_WALL_SUSPECTED candidates).
    """
    cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    edge = m.free & (cv2.dilate(m.outside.astype(np.uint8), cross) > 0)
    merged = cv2.dilate(edge.astype(np.uint8), np.ones((5, 5), np.uint8))
    n, lab = cv2.connectedComponents(merged, connectivity=8)
    out, wide = [], []
    for i in range(1, n):
        rr, cc = np.nonzero((lab == i) & edge)
        if len(rr) < 3:
            continue
        xz = g.cell_xz(rr, cc)
        span = xz.max(0) - xz.min(0) + g.res
        along = int(np.argmax(span))
        c = np.median(xz, axis=0)
        a, b = c.copy(), c.copy()
        a[along], b[along] = xz[:, along].min() - g.res / 2, xz[:, along].max() + g.res / 2
        item = {"segment": [a.round(3).tolist(), b.round(3).tolist()], "width_m": round(float(span[along]), 3),
                "center": c.round(3).tolist()}
        if min_w <= span[along] <= max_w:
            out.append(item)
        elif span[along] > max_w:
            wide.append(item)
    return out, wide
