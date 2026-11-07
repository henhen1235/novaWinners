"""Obstacles: body-band occupied blobs away from walls (PLAN.md "obstacles.py")."""

from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage
from shapely.geometry import Polygon

from .geometry import ring
from .grid import Grid, Masks, components, vectorize
from .walls import wall_mask

LEVEL = "L1"
MIN_AREA_M2 = 0.04
EDGE_CLUTTER_M2 = 6.0
RECT_TOL = 0.2


def _kind(height: float, area: float) -> str:
    if height >= 2.2:
        return "pillar" if area <= 0.6 else "unknown"
    return "furniture"


def detect_obstacles(g: Grid, m: Masks, walls: list[dict]) -> list[dict]:
    """Contract Obstacle dicts, sorted by centroid (z, x)."""
    near = cv2.dilate(wall_mask(g, walls).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    n, lab, st = components(m.occupied & ~near, 4)
    cell = g.res**2
    found = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        on_border = x == 0 or y == 0 or x + w == g.shape[1] or y + h == g.shape[0]
        if area * cell > EDGE_CLUTTER_M2 and on_border:
            continue  # ponytail: scan-edge clutter; real big furniture near the border is lost too
        sub = ndimage.binary_fill_holes(lab[y:y + h, x:x + w] == i)
        if sub.sum() * cell < MIN_AREA_M2:
            continue
        full = np.zeros(g.shape, bool)
        full[y:y + h, x:x + w] = sub
        mp = vectorize(full, g)
        if mp.is_empty:
            continue
        poly = Polygon(max(mp.geoms, key=lambda p: p.area).exterior)
        rect = poly.minimum_rotated_rectangle
        comp_area = sub.sum() * cell
        if isinstance(rect, Polygon) and abs(rect.area - comp_area) <= RECT_TOL * comp_area:
            poly = rect
        if not poly.is_valid or poly.area <= 0.01:
            continue
        height = float(g.maxh[full].max())
        found.append((poly, height))
    found.sort(key=lambda t: (round(t[0].centroid.y, 3), round(t[0].centroid.x, 3)))
    return [{"id": f"o_{i + 1:03d}", "level": LEVEL, "polygon": ring(p), "height_m": round(h, 3),
             "kind": _kind(h, p.area), "passable": False} for i, (p, h) in enumerate(found)]
