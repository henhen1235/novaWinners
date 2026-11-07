"""RoomScanner Mesh-mode keyframes (ios/README.md): pose, intrinsics, JPEG and LiDAR depth.

Lifts a 2D box to a 3D point in the ARKit world frame, which is the mesh.ply frame.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

DEPTH_RANGE_M = (0.1, 8.0)  # 0 = no reading; LiDAR is unreliable past ~5 m but keep a margin
BOX_CORE = 0.4  # use the central 40% of the box: edges of a sign box often hit the wall behind


@dataclass(frozen=True)
class Frame:
    n: int
    pose: np.ndarray  # 4x4 camera_to_world, ARKit camera axes (x right, y up, z toward viewer)
    K: tuple[float, float, float, float]  # fx, fy, cx, cy in image pixels
    image: Path | None
    width: int
    height: int
    depth: np.ndarray | None  # (dh, dw) float32 meters, 0 where invalid


def load_keyframes(kf_dir: Path) -> list[Frame]:
    """Read frames.jsonl in kf_dir. Frames with a missing image are skipped."""
    kf_dir = Path(kf_dir)
    frames = []
    for line in (kf_dir / "frames.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        img = kf_dir / r["image"]
        if not img.exists():
            continue
        depth = None
        if r.get("depth") and (kf_dir / r["depth"]).exists():
            d = cv2.imread(str(kf_dir / r["depth"]), cv2.IMREAD_UNCHANGED)
            depth = d.astype(np.float32) / 1000.0 if d is not None else None
        frames.append(Frame(int(r["n"]), np.asarray(r["camera_to_world"], float).reshape(4, 4),
                            tuple(float(k) for k in r["intrinsics"]), img, int(r["width"]),
                            int(r["height"]), depth))
    return frames


def lift(frame: Frame, box) -> np.ndarray | None:
    """World xyz of the box center at the median valid LiDAR depth in the box core, or None."""
    if frame.depth is None:
        return None
    x0, y0, x1, y1 = box
    dh, dw = frame.depth.shape
    sx, sy = dw / frame.width, dh / frame.height
    u, v = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = max((x1 - x0) * BOX_CORE / 2, 1 / sx), max((y1 - y0) * BOX_CORE / 2, 1 / sy)
    c0, c1 = int(np.clip((u - hw) * sx, 0, dw - 1)), int(np.clip(np.ceil((u + hw) * sx), 1, dw))
    r0, r1 = int(np.clip((v - hh) * sy, 0, dh - 1)), int(np.clip(np.ceil((v + hh) * sy), 1, dh))
    z = frame.depth[r0:r1, c0:c1]
    z = z[(z > DEPTH_RANGE_M[0]) & (z < DEPTH_RANGE_M[1])]
    if not z.size:
        return None
    z = float(np.median(z))
    fx, fy, cx, cy = frame.K
    cam = np.array([(u - cx) / fx * z, -(v - cy) / fy * z, -z, 1.0])
    return (frame.pose @ cam)[:3]
