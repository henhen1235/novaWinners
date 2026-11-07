"""Open-vocabulary detection on keyframes (OWLv2), lifted to 3D and merged into POIs.

Optional: needs requirements-cv.txt (torch, torchvision, transformers). Detections are cached
per image under data/cv_cache/<sha256>.json, so reruns skip the model entirely.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np

from .keyframes import Frame, lift, load_keyframes
from .orient import apply

MODEL_ID = "google/owlv2-base-patch16-ensemble"
PROMPTS = {"exit_sign": "an exit sign", "fire_extinguisher": "a fire extinguisher",
           "aed": "an AED defibrillator", "alarm_pull": "a fire alarm pull station",
           "door": "a door", "stairs": "stairs", "person": "a person"}
POI_KIND = {"exit_sign": "fire_exit_sign", "fire_extinguisher": "fire_extinguisher", "aed": "aed",
            "alarm_pull": "alarm_pull"}  # door/stairs/person: evidence in extensions.cv only
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cv_cache"
CACHE_MIN_SCORE = 0.1  # cached; the keep threshold below can be retuned without rerunning
MIN_SCORE = 0.2
# Tuned on the hall scan (2026-11-07): every extinguisher/pull hit below 0.35 was a thermostat,
# card reader, red bag or bottle, so those classes need a higher score plus a colour check.
CLASS_MIN_SCORE = {"fire_extinguisher": 0.35, "alarm_pull": 0.35, "aed": 0.35}
CLUSTER_M = 0.5
KEEP_VIEWS, KEEP_SCORE = 2, 0.45
MAX_FRAMES = 120
NMS_IOU = 0.3


def _color_ok(img: np.ndarray, d: dict) -> bool:
    """Colour gate on the central 60% of the box: exit signs are lit green (or red) letters,
    extinguishers and pull stations are red. On the hall scan OWLv2's top "exit sign" (0.86)
    was a white fire-extinguisher location sign on a red wall; the green share split real
    signs (>= 0.3) from false ones (0.0-0.12) cleanly.
    ponytail: hue thresholds, a red-letter exit sign on a red wall would pass; add OCR if needed."""
    x0, y0, x1, y1 = d["box"]
    w, h = x1 - x0, y1 - y0
    core = img[int(max(0, y0 + 0.2 * h)):int(y1 - 0.2 * h) + 1, int(max(0, x0 + 0.2 * w)):int(x1 - 0.2 * w) + 1]
    if not core.size:
        return False
    hsv = cv2.cvtColor(core, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(int)
    vivid = (hsv[:, 1] > 100) & (hsv[:, 2] > 90)
    green = (vivid & (hsv[:, 0] >= 35) & (hsv[:, 0] <= 95)).mean()
    red = (vivid & ((hsv[:, 0] <= 10) | (hsv[:, 0] >= 165))).mean()
    if d["label"] == "exit_sign":
        return green >= 0.25 or red >= 0.4
    return red >= 0.3


COLOR_GATED = ("exit_sign", "fire_extinguisher", "alarm_pull")


def _upright_k(pose: np.ndarray) -> int:
    """Clockwise quarter turns that put world up at the top of the image.

    World up in camera axes is row 1 of the rotation; in image axes (u right, v down) it is
    (x, -y). The phone is usually held in portrait while the sensor image is landscape.
    """
    ux, uy = pose[1, 0], -pose[1, 1]
    if abs(ux) > abs(uy):
        return 1 if ux < 0 else 3
    return 0 if uy < 0 else 2


def _unrotate(box, k: int, w: int, h: int) -> list[float]:
    """Map an xyxy box from the image turned k quarter turns clockwise back to the original."""
    x0, y0, x1, y1 = box
    if k == 1:
        return [y0, h - 1 - x1, y1, h - 1 - x0]
    if k == 2:
        return [w - 1 - x1, h - 1 - y1, w - 1 - x0, h - 1 - y0]
    if k == 3:
        return [w - 1 - y1, x0, w - 1 - y0, x1]
    return [x0, y0, x1, y1]


ROT = {1: cv2.ROTATE_90_CLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_COUNTERCLOCKWISE}


def _upright(img: np.ndarray, k: int) -> np.ndarray:
    return cv2.rotate(img, ROT[k]) if k else img


class Owl:
    """OWLv2 detector with a per-image JSON cache. Raises ImportError without torch/transformers."""

    def __init__(self) -> None:
        import torch  # noqa: F401  (fail fast so the caller can warn CV_UNAVAILABLE)
        import transformers  # noqa: F401

        self.model = None
        self.hits = self.misses = 0

    def _load(self) -> None:
        import torch
        from transformers import AutoProcessor, Owlv2ForObjectDetection

        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.proc = AutoProcessor.from_pretrained(MODEL_ID)
        self.model = Owlv2ForObjectDetection.from_pretrained(MODEL_ID).to(self.device).eval()

    def __call__(self, frame: Frame) -> list[dict]:
        k = _upright_k(frame.pose)
        raw = frame.image.read_bytes()
        key = hashlib.sha256(raw + f"|{MODEL_ID}|{sorted(PROMPTS.items())}|rot{k}".encode()).hexdigest()
        path = CACHE_DIR / f"{key}.json"
        if path.exists():
            self.hits += 1
            return json.loads(path.read_text())
        self.misses += 1
        dets = self._detect(cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR), k)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dets))
        return dets

    def _detect(self, bgr: np.ndarray, k: int) -> list[dict]:
        import torch
        from PIL import Image

        if self.model is None:
            self._load()
        h, w = bgr.shape[:2]
        im = Image.fromarray(cv2.cvtColor(_upright(bgr, k), cv2.COLOR_BGR2RGB))
        labels = list(PROMPTS)
        inp = self.proc(text=[[PROMPTS[x] for x in labels]], images=im, return_tensors="pt").to(self.device)
        with torch.no_grad():
            out = self.model(**inp)
        res = self.proc.post_process_grounded_object_detection(
            outputs=out, threshold=CACHE_MIN_SCORE, target_sizes=[(im.height, im.width)])[0]
        dets = [{"label": labels[int(lb)], "score": round(float(s), 4),
                 "box": [round(float(c), 1) for c in _unrotate(b.tolist(), k, w, h)]}
                for lb, s, b in zip(res["labels"], res["scores"], res["boxes"])]
        return _nms(dets)


def _nms(dets: list[dict]) -> list[dict]:
    """Per-class NMS: OWLv2 returns many overlapping boxes for one object."""
    keep = []
    for lb in {d["label"] for d in dets}:
        ds = [d for d in dets if d["label"] == lb]
        xywh = [[d["box"][0], d["box"][1], d["box"][2] - d["box"][0], d["box"][3] - d["box"][1]] for d in ds]
        idx = cv2.dnn.NMSBoxes(xywh, [d["score"] for d in ds], 0.0, NMS_IOU)
        keep += [ds[int(i)] for i in np.asarray(idx).ravel()]
    return sorted(keep, key=lambda d: -d["score"])


def _cluster(points: list[dict]) -> list[dict]:
    """Greedy same-class clustering in scene XZ, strongest detection first."""
    clusters: list[dict] = []
    for p in sorted(points, key=lambda q: -q["score"]):
        near = [c for c in clusters if c["label"] == p["label"]
                and np.hypot(*(np.mean(c["xyz"], 0)[[0, 2]] - p["xyz"][[0, 2]])) <= CLUSTER_M]
        if near:
            near[0]["xyz"].append(p["xyz"])
            near[0]["frames"].add(p["frame"])
        else:
            clusters.append({"label": p["label"], "xyz": [p["xyz"]], "frames": {p["frame"]},
                             "max_score": p["score"]})
    out = []
    for c in clusters:
        xyz = np.median(c["xyz"], 0)
        views = len(c["frames"])
        out.append({"label": c["label"], "kind": POI_KIND.get(c["label"]),
                    "position": [round(float(xyz[0]), 3), round(float(xyz[2]), 3)],
                    "height_m": round(float(xyz[1]), 3), "views": views, "detections": len(c["xyz"]),
                    "max_score": round(c["max_score"], 3), "frames": sorted(c["frames"]),
                    "kept": views >= KEEP_VIEWS or c["max_score"] >= KEEP_SCORE})
    return sorted(out, key=lambda c: (not c["kept"], -c["max_score"]))


def _subsample(frames: list[Frame], n: int) -> list[Frame]:
    if len(frames) <= n:
        return frames
    return [frames[i] for i in np.unique(np.linspace(0, len(frames) - 1, n).round().astype(int))]


def run_cv(kf_dir: Path, input_to_scene: np.ndarray, detector=None, out_dir: Path | None = None,
           level: str = "L1", max_frames: int = MAX_FRAMES) -> dict:
    """Detect, lift, cluster. Returns {points_of_interest, exit_signs [[x, z]], extensions}.

    detector(frame) -> [{label, score, box xyxy in image pixels}]; defaults to Owl() (may raise
    ImportError). Keyframe poses are in the mesh's input frame; input_to_scene maps to scene.
    """
    t0 = time.perf_counter()
    detector = detector or Owl()
    all_frames = load_keyframes(kf_dir)
    frames = _subsample(all_frames, max_frames)
    counts: dict[str, int] = {}
    rejected: dict[str, int] = {}
    points, per_frame, no_depth = [], {}, 0
    for i, f in enumerate(frames):
        dets = [d for d in detector(f) if d["label"] in PROMPTS
                and d["score"] >= CLASS_MIN_SCORE.get(d["label"], MIN_SCORE)]
        if any(d["label"] in COLOR_GATED for d in dets):
            img = cv2.imread(str(f.image))
            ok = [d["label"] not in COLOR_GATED or _color_ok(img, d) for d in dets]
            for d, k in zip(dets, ok):
                rejected[d["label"]] = rejected.get(d["label"], 0) + (not k)
            dets = [d for d, k in zip(dets, ok) if k]
        per_frame[f.n] = (f, dets)
        for d in dets:
            counts[d["label"]] = counts.get(d["label"], 0) + 1
            xyz = lift(f, d["box"])
            if xyz is None:
                no_depth += 1
                continue
            points.append({**d, "frame": f.n, "xyz": apply(input_to_scene, xyz[None])[0]})
        if (i + 1) % 10 == 0 or i + 1 == len(frames):
            print(f"cv: {i + 1}/{len(frames)} keyframes, {sum(counts.values())} detections", flush=True)
    t_detect = time.perf_counter() - t0
    clusters = _cluster(points)
    pois = [{"id": f"poi_{k + 1:03d}", "level": level, "position": c["position"], "kind": c["kind"]}
            for k, c in enumerate(c for c in clusters if c["kept"] and c["kind"])]
    if out_dir is not None:
        _debug_crops(Path(out_dir) / "debug" / "cv", per_frame, clusters)
    ext = {"model": MODEL_ID, "prompts": PROMPTS, "frames_total": len(all_frames), "frames_used": len(frames),
           "detections": counts, "rejected_by_color": rejected, "lifted": len(points), "no_depth": no_depth,
           "thresholds": {"min_score": MIN_SCORE, "class_min_score": CLASS_MIN_SCORE, "cluster_m": CLUSTER_M, "keep_views": KEEP_VIEWS,
                          "keep_score": KEEP_SCORE},
           "clusters": clusters, "poi_ids": [p["id"] for p in pois],
           "cache": {"hits": getattr(detector, "hits", None), "misses": getattr(detector, "misses", None)},
           "timings_s": {"detect_and_lift": round(t_detect, 2), "total": round(time.perf_counter() - t0, 2)}}
    signs = [c["position"] for c in clusters if c["kept"] and c["label"] == "exit_sign"]
    people = [{"position": [round(float(p["xyz"][0]), 3), round(float(p["xyz"][2]), 3)], "score": p["score"],
               "frame": p["frame"]} for p in points if p["label"] == "person"]
    return {"points_of_interest": pois, "exit_signs": signs, "people": people, "extensions": ext}


COLORS = {"exit_sign": (0, 200, 0), "fire_extinguisher": (0, 0, 255), "aed": (255, 0, 255),
          "alarm_pull": (0, 128, 255), "door": (255, 128, 0), "stairs": (200, 200, 0), "person": (0, 255, 255)}


def _debug_crops(d: Path, per_frame: dict, clusters: list[dict], n: int = 6) -> None:
    """Save the n keyframes with the strongest kept detections (one per label first), boxes drawn."""
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("kf_*.jpg"):
        old.unlink()
    picks: list[int] = []
    for c in clusters:  # kept clusters first, best first, one frame per cluster
        if len(picks) >= n:
            break
        best = max(c["frames"], key=lambda fn: max((x["score"] for x in per_frame[fn][1]
                                                    if x["label"] == c["label"]), default=0))
        if best not in picks:
            picks.append(best)
    for fn in picks:
        f, dets = per_frame[fn]
        img = cv2.imread(str(f.image))
        for x in dets:
            x0, y0, x1, y1 = (int(v) for v in x["box"])
            cv2.rectangle(img, (x0, y0), (x1, y1), COLORS[x["label"]], 3)
        img = _upright(img, _upright_k(f.pose))
        k = _upright_k(f.pose)
        for x in dets:  # labels after rotation so the text reads upright
            h, w = f.height, f.width
            bx = _rotate_box(x["box"], k, w, h)
            cv2.putText(img, f"{x['label']} {x['score']:.2f}", (int(bx[0]), max(15, int(bx[1]) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLORS[x["label"]], 2)
        cv2.imwrite(str(d / f"kf_{fn}.jpg"), cv2.resize(img, None, fx=0.5, fy=0.5))


def _rotate_box(box, k: int, w: int, h: int) -> list[float]:
    """Inverse of _unrotate: original xyxy box -> box in the image turned k times clockwise."""
    x0, y0, x1, y1 = box
    if k == 1:
        return [h - 1 - y1, x0, h - 1 - y0, x1]
    if k == 2:
        return [w - 1 - x1, h - 1 - y1, w - 1 - x0, h - 1 - y0]
    if k == 3:
        return [y0, w - 1 - x1, y1, w - 1 - x0]
    return [x0, y0, x1, y1]
