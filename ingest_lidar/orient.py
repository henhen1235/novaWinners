"""Find "up" and a Manhattan yaw for a scan; output a proper rigid transform.

Input frame: whatever the file uses. Output frame: the scene frame (meters,
Y up, floor at y = 0, dominant wall direction along X, robust min x/z at 0).
The rotation always has det +1, so the building is never mirrored.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .load import Scan

COS10 = np.cos(np.radians(10.0))
SIN10 = np.sin(np.radians(10.0))
ALIGNED = 0.95  # |n . a| above this: face counts as perpendicular to axis a
BIN = 0.05


@dataclass(frozen=True)
class Orientation:
    """matrix: 4x4 mapping input coords to scene coords (column vectors)."""

    matrix: np.ndarray
    ceiling_height_m: float
    report: dict


def surface_elements(scan: Scan, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
    """Return (centers, unit normals, weights, signed) from faces, or PCA normals for point clouds."""
    if scan.faces is not None:
        tri = scan.vertices[scan.faces]
        cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        dbl = np.linalg.norm(cross, axis=1)
        keep = dbl > 1e-12
        return tri[keep].mean(1), cross[keep] / dbl[keep, None], 0.5 * dbl[keep], True
    pts = scan.vertices
    if len(pts) > 200_000:
        pts = pts[rng.choice(len(pts), 200_000, replace=False)]
    _, nn = cKDTree(pts).query(pts, k=12)
    nb = pts[nn] - pts[nn].mean(1, keepdims=True)
    _, vecs = np.linalg.eigh(np.einsum("nki,nkj->nij", nb, nb))
    return pts, vecs[:, :, 0], np.ones(len(pts)), False


def _support(cands: np.ndarray, sample: np.ndarray) -> np.ndarray:
    out = np.empty(len(cands))
    for i in range(0, len(cands), 200):
        out[i:i + 200] = (np.abs(cands[i:i + 200] @ sample.T) > COS10).sum(1)
    return out


def _refine(a: np.ndarray, sample: np.ndarray) -> np.ndarray:
    for _ in range(3):
        d = sample @ a
        al = np.abs(d) > COS10
        a = (sample[al] * np.sign(d[al])[:, None]).sum(0)
        a = a / np.linalg.norm(a)
    return a


def manhattan_frame(normals: np.ndarray, weights: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Three orthonormal dominant directions (rows), strongest first."""
    p = weights / weights.sum()
    sample = normals[rng.choice(len(normals), min(50_000, len(normals)), p=p)]
    cands = normals[rng.choice(len(normals), min(1000, len(normals)), p=p)]
    a1 = _refine(cands[np.argmax(_support(cands, sample))], sample)
    perp = cands[np.abs(cands @ a1) < SIN10]
    if len(perp) == 0:
        perp = np.cross(a1, np.eye(3)[np.argmin(np.abs(a1))])[None]
    a2 = _refine(perp[np.argmax(_support(perp, sample))], sample)
    a2 = a2 - (a2 @ a1) * a1
    a2 /= np.linalg.norm(a2)
    return np.stack([a1, a2, np.cross(a1, a2)])


def _axis_cues(a: np.ndarray, centers: np.ndarray, normals: np.ndarray, w: np.ndarray, signed: bool) -> dict:
    """Measure how much axis a looks like gravity. See PLAN.md "orient.py"."""
    off = centers @ a
    nd = normals @ a
    al = np.abs(nd) > ALIGNED
    if al.sum() < 10:
        return {"score": -1.0, "ext": 0.0}
    oa, wa = off[al], w[al]
    edges = np.arange(oa.min(), oa.max() + 2 * BIN, BIN)
    hist, _ = np.histogram(oa, edges, weights=wa)
    sig = np.nonzero(hist >= 0.1 * hist.max())[0]
    lo, hi = edges[sig[0]] + BIN / 2, edges[sig[-1]] + BIN / 2
    near = (np.abs(oa - lo) < 0.1) | (np.abs(oa - hi) < 0.1)
    mid = (oa > lo + 0.2) & (oa < hi - 0.2)
    mid_area = wa[mid].sum()
    asym = float((wa[mid] * np.sign(nd[al][mid])).sum() / mid_area) if (signed and mid_area > 0) else 0.0
    return {"lo": float(lo), "hi": float(hi), "ext": float(hi - lo),
            "planar": float(wa[near].sum() / wa.sum()), "asym": asym,
            "mid_frac": float(mid_area / w.sum()),
            "near_lo": float(w[(off > lo + 0.15) & (off < lo + 1.2)].sum()),
            "near_hi": float(w[(off > hi - 1.2) & (off < hi - 0.15)].sum()), "score": 0.0}


def _parse_up(up: str) -> np.ndarray:
    sign = -1.0 if up.startswith("-") else 1.0
    return sign * np.eye(3)["xyz".index(up.lstrip("+-").lower())]


def _score(a: np.ndarray, c: dict, max_ext: float) -> float:
    if c["score"] < 0:
        return -1.0
    return (c["planar"] + abs(c["asym"]) * min(1.0, c["mid_frac"] / 0.005)
            + 0.5 * float(2.0 <= c["ext"] <= 4.8) + 0.3 * (1 - c["ext"] / max_ext) + 0.2 * abs(a[1]))


def orient(scan: Scan, up_override: str | None = None, seed: int = 0) -> Orientation:
    """Estimate the input->scene transform. up_override like "+y" or "-z" skips up detection."""
    rng = np.random.default_rng(seed)
    centers, normals, w, signed = surface_elements(scan, rng)
    frame = manhattan_frame(normals, w, rng)
    cues = [_axis_cues(a, centers, normals, w, signed) for a in frame]
    max_ext = max(c["ext"] for c in cues) or 1.0
    for a, c in zip(frame, cues):
        c["score"] = _score(a, c, max_ext)
    if up_override:
        target = _parse_up(up_override)
        k = int(np.argmax(np.abs(frame @ target)))
        up = frame[k] * np.sign(frame[k] @ target)
    else:
        k = int(np.argmax([c["score"] for c in cues]))
        c = cues[k]
        if abs(c["asym"]) >= 0.3:
            up = frame[k] * np.sign(c["asym"])
        else:  # furniture clutters the space just above the floor
            up = frame[k] if c["near_lo"] >= c["near_hi"] else -frame[k]
    c = cues[k]
    floor_off = c["lo"] if up @ frame[k] > 0 else -c["hi"]
    horiz = [i for i in range(3) if i != k]
    ex = frame[max(horiz, key=lambda i: cues[i]["ext"])]
    ex = ex * np.sign(ex[np.argmax(np.abs(ex))])
    rot = np.stack([ex, up, np.cross(ex, up)])
    p = centers @ rot.T
    m = np.eye(4)
    m[:3, :3] = rot
    m[:3, 3] = [-np.percentile(p[:, 0], 0.5), -floor_off, -np.percentile(p[:, 2], 0.5)]
    report = {"up_axis_index": k, "up_input_frame": up.round(4).tolist(), "axes": frame.round(4).tolist(),
              "cues": cues, "override": up_override, "det": float(np.linalg.det(rot))}
    return Orientation(m, float(c["ext"]), report)


def apply(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Apply a 4x4 transform to (n, 3) points."""
    return pts @ m[:3, :3].T + m[:3, 3]
