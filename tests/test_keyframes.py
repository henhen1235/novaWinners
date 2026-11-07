"""Keyframe lift round trip and CV merge, no torch or network (the detector is mocked)."""

import json

import cv2
import numpy as np

from ingest_lidar import detect
from ingest_lidar.keyframes import Frame, lift, load_keyframes

W, H, DW, DH = 1440, 1080, 256, 192
K = (1000.0, 1000.0, 720.0, 540.0)


def _pose(yaw_deg: float, pos) -> np.ndarray:
    """camera_to_world for a camera at pos looking along -z rotated by yaw about +y (ARKit axes)."""
    a = np.radians(yaw_deg)
    m = np.eye(4)
    m[:3, :3] = [[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]]
    m[:3, 3] = pos
    return m


def _project(c2w: np.ndarray, p) -> tuple[float, float, float]:
    cam = np.linalg.inv(c2w) @ np.append(p, 1.0)
    z = -cam[2]
    fx, fy, cx, cy = K
    return cam[0] / z * fx + cx, -cam[1] / z * fy + cy, z


def _write_frames(tmp_path, poses, depth_m):
    lines = []
    for n, c2w in enumerate(poses):
        cv2.imwrite(str(tmp_path / f"kf_{n}.jpg"), np.full((H, W, 3), (40, 220, 40), np.uint8))  # lit green
        cv2.imwrite(str(tmp_path / f"kf_{n}_depth.png"), np.full((DH, DW), int(depth_m[n] * 1000), np.uint16))
        lines.append(json.dumps({"n": n, "t": float(n), "image": f"kf_{n}.jpg", "depth": f"kf_{n}_depth.png",
                                 "width": W, "height": H, "depth_width": DW, "depth_height": DH,
                                 "intrinsics": list(K), "camera_to_world": c2w.ravel().tolist()}))
    (tmp_path / "frames.jsonl").write_text("\n".join(lines) + "\n")


def test_lift_round_trip(tmp_path):
    target = np.array([1.3, 1.9, -4.2])
    c2w = _pose(20, [0.2, 1.5, 0.1])
    u, v, z = _project(c2w, target)
    _write_frames(tmp_path, [c2w], [z])
    (f,) = load_keyframes(tmp_path)
    assert np.allclose(f.pose, c2w) and f.depth.dtype == np.float32
    got = lift(f, (u - 30, v - 20, u + 30, v + 20))
    assert np.linalg.norm(got - target) < 0.01


def test_lift_no_depth():
    f = Frame(0, np.eye(4), K, None, W, H, np.zeros((DH, DW), np.float32))
    assert lift(f, (700, 500, 740, 580)) is None


def test_run_cv_mock_detector(tmp_path):
    """One exit sign seen from two frames clusters to one POI; a single weak door is evidence only."""
    sign = np.array([0.5, 2.2, -5.0])
    poses = [_pose(0, [0, 1.5, 0]), _pose(-10, [1.0, 1.5, 0.5])]
    proj = [_project(p, sign) for p in poses]
    _write_frames(tmp_path, poses, [z for _, _, z in proj])

    class Fake:
        def __call__(self, frame):
            u, v, _ = proj[frame.n]
            return [{"label": "exit_sign", "score": 0.4, "box": [u - 25, v - 15, u + 25, v + 15]},
                    {"label": "door", "score": 0.3, "box": [u - 25, v - 15, u + 25, v + 15]}]

    res = detect.run_cv(tmp_path, np.eye(4), detector=Fake(), out_dir=None)
    pois = res["points_of_interest"]
    assert [p["kind"] for p in pois] == ["fire_exit_sign"]
    assert np.hypot(pois[0]["position"][0] - 0.5, pois[0]["position"][1] + 5.0) < 0.05
    assert res["exit_signs"] == [pois[0]["position"]]
    assert res["extensions"]["detections"] == {"exit_sign": 2, "door": 2}
    assert res["extensions"]["clusters"][0]["views"] == 2


def test_color_gate():
    """A white sign on a red wall is not an exit sign; lit green letters are."""
    box = {"label": "exit_sign", "score": 0.8, "box": [10, 10, 60, 40]}
    white = np.full((80, 80, 3), (40, 40, 200), np.uint8)
    white[10:40, 10:60] = 245
    green = white.copy()
    green[10:40, 10:60] = (60, 230, 60)
    assert not detect._color_ok(white, box) and detect._color_ok(green, box)
    assert detect._color_ok(np.full((80, 80, 3), (30, 30, 210), np.uint8), {**box, "label": "alarm_pull"})


def test_upright_rotation_round_trip():
    """A box drawn on the upright image maps back onto the same pixels of the sensor image."""
    img = np.zeros((H, W), np.uint8)
    img[100:200, 300:360] = 255
    for k in range(4):
        rot = detect._upright(img, k)
        ys, xs = np.nonzero(rot)
        box = [xs.min(), ys.min(), xs.max(), ys.max()]
        assert detect._unrotate(box, k, W, H) == [300, 100, 359, 199]
        assert detect._rotate_box([300, 100, 359, 199], k, W, H) == box
    portrait = np.eye(4)
    portrait[:3, :3] = [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]  # camera x points world down
    assert detect._upright_k(portrait) == 1 and detect._upright_k(np.eye(4)) == 0


def test_remove_people_keeps_pillars_and_undetected_blobs():
    from ingest_lidar.grid import Grid
    from ingest_lidar.load import Scan
    from ingest_lidar.people import remove_people

    shape = (100, 100)  # 5 m x 5 m at 5 cm
    body, maxh = np.zeros(shape, np.int32), np.zeros(shape, np.float32)
    for (r, c), h in {(20, 20): 1.7, (20, 30): 2.5, (70, 70): 1.6}.items():  # person, pillar, plant
        body[r:r + 8, c:c + 8], maxh[r:r + 8, c:c + 8] = 10, h
    g = Grid(0.0, 0.0, 0.05, np.ones(shape, np.int32), body, np.zeros(shape, np.int32), maxh)
    scan = Scan(np.zeros((0, 3)), None, None)
    people = [{"position": [1.2, 1.2], "score": 0.5, "frame": 3}]  # on the person, 0.3 m from the pillar
    g2, removed, mask = remove_people(scan, np.eye(4), g, people)
    assert len(removed) == 1 and removed[0]["best_person"]["frame"] == 3
    assert np.hypot(removed[0]["position"][0] - 1.2, removed[0]["position"][1] - 1.2) < 0.1
    assert g2.body[22, 22] == 0 and g2.body[22, 32] == 10 and g2.body[72, 72] == 10
    assert mask.sum() == 64 and g.body[22, 22] == 10  # input grid untouched
