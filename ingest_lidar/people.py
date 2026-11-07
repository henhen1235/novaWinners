"""Remove people baked into the ARKit mesh, using person detections from the keyframes.

A person who stood still during the scan becomes a body-height blob in the occupancy grid, so
the pipeline turns them into an obstacle and cuts the walkable floor. A blob is removed only if
it is person-sized, unlabeled by ARKit (not table/seat/wall) and a person was detected on it.
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from . import grid as G
from .load import ARKIT_CLASSES, Scan
from .orient import apply

MAX_M2 = 0.8
TOP_M = (1.0, 2.1)
NEAR_M = 0.6
MIN_NONE_FRAC = 0.5  # share of ARKit "none" faces among the blob's body-height faces
MAX_FURNITURE_FRAC = 0.25  # wall + table + seat share; above this it is furniture or wall
FURNITURE = (1, 4, 5)


def _blob_classes(scan: Scan, matrix: np.ndarray, g: G.Grid, lab: np.ndarray, n: int) -> np.ndarray:
    """(n, 8) ARKit face-class counts per blob, faces with centroid at body height."""
    out = np.zeros((n, len(ARKIT_CLASSES)), np.int64)
    if scan.face_classes is None or scan.faces is None:
        return out
    c = apply(matrix, scan.vertices[scan.faces].mean(1))
    body = (c[:, 1] >= G.BODY_BAND[0]) & (c[:, 1] <= G.BODY_BAND[1])
    rc = g.rc(c[body][:, [0, 2]])
    ok = (rc[:, 0] >= 0) & (rc[:, 0] < g.shape[0]) & (rc[:, 1] >= 0) & (rc[:, 1] < g.shape[1])
    ids = lab[rc[ok, 0], rc[ok, 1]]
    cls = scan.face_classes[body][ok].astype(np.int64)
    return np.bincount(ids * len(ARKIT_CLASSES) + cls, minlength=n * len(ARKIT_CLASSES)).reshape(n, -1)


def remove_people(scan: Scan, matrix: np.ndarray, g: G.Grid, people: list[dict]
                  ) -> tuple[G.Grid, list[dict], np.ndarray]:
    """Clear body-band counts of person blobs. people: [{position [x, z], score, frame}] in scene.

    Returns (grid without those blobs, removal records, removed-cell mask).
    """
    removed_mask = np.zeros(g.shape, bool)
    if not people:
        return g, [], removed_mask
    n, lab, st = G.components(g.body >= 2, conn=8)
    cls = _blob_classes(scan, matrix, g, lab, n)
    pxz = np.array([p["position"] for p in people], float)
    removed = []
    for i in range(1, n):
        area = st[i, 4] * g.res ** 2
        if area > MAX_M2:
            continue
        cells = lab == i
        top = float(g.maxh[cells].max())
        tot = cls[i].sum()
        none = cls[i, 0] / tot if tot else 1.0
        furn = cls[i, list(FURNITURE)].sum() / tot if tot else 0.0
        if not (TOP_M[0] <= top <= TOP_M[1] and none >= MIN_NONE_FRAC and furn <= MAX_FURNITURE_FRAC):
            continue
        r, c = np.nonzero(cells)
        xz = g.cell_xz(r, c)
        d = np.min(np.hypot(xz[:, None, 0] - pxz[None, :, 0], xz[:, None, 1] - pxz[None, :, 1]), 0)
        near = np.nonzero(d <= NEAR_M)[0]
        if not len(near):
            continue
        best = near[np.argmax([people[k]["score"] for k in near])]
        removed_mask |= cells
        removed.append({"position": [round(float(v), 3) for v in xz.mean(0)], "area_m2": round(area, 3),
                        "top_m": round(top, 2), "none_frac": round(float(none), 2),
                        "furniture_frac": round(float(furn), 2), "person_dets_near": int(len(near)),
                        "best_person": {"score": people[best]["score"], "frame": people[best]["frame"],
                                        "dist_m": round(float(d[best]), 2)}})
    body = np.where(removed_mask, 0, g.body).astype(g.body.dtype)
    return replace(g, body=body), removed, removed_mask


def write_people_debug(path, g: G.Grid, before: np.ndarray, after: np.ndarray, removed: np.ndarray,
                       people: list[dict], occupied: np.ndarray) -> None:
    """Side by side walkable before (left) / after (right); removed blobs red, persons yellow."""
    def panel(walk: np.ndarray) -> np.ndarray:
        img = np.full(g.shape + (3,), 255, np.uint8)
        img[occupied] = (60, 60, 60)
        img[walk] = (140, 210, 140)
        img[removed] = (0, 0, 230)
        for p in people:
            r, c = g.rc(np.array(p["position"]))[0]
            if 0 <= r < g.shape[0] and 0 <= c < g.shape[1]:
                cv2.circle(img, (int(c), int(r)), 2, (0, 200, 255), -1)
        return cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)

    a, b = panel(before), panel(after)
    for im, t in ((a, "walkable before"), (b, f"after: {int(removed.sum())} cells of people removed")):
        cv2.putText(im, t, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    cv2.imwrite(str(path), np.hstack([a, np.full((a.shape[0], 8, 3), 0, np.uint8), b]))
