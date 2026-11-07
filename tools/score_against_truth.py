"""Score a pipeline scene against a synthetic scan's ground truth (PLAN.md,
"Synthetic scan generator spec", scoring paragraph, and "Scoring targets").

Frames: truth_to_mesh (truth_transform.json) maps truth -> input mesh;
scene["extensions"]["lidar"]["input_to_scene"] maps input mesh -> scene. Both
are 4x4 row-major, meters. M = input_to_scene @ truth_to_mesh maps truth to
scene. Up error uses M's rotation; 2D metrics map predicted XZ geometry back to
the truth frame by undoing only the yaw (closest proper 2D rotation) and the
translation of M, so a mirrored prediction is NOT forgiven.

    python -m tools.score_against_truth --bench data/synth -o out/bench
    python -m tools.score_against_truth --bench data/synth -o out/bench --perfect   # scorer self-check
"""

from __future__ import annotations

import argparse
import json
import math
import time
import traceback
from pathlib import Path
from typing import Callable

import numpy as np
import shapely
from scipy.optimize import linear_sum_assignment
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import unary_union

from contract.validate_scene import validate

DOOR_MATCH_M = 0.5
WALL_SAMPLE_M = 0.1
OBSTACLE_MIN_HEIGHT_M = 0.5
TIER_A = (0.015, 0.05)  # (noise sigma m, hole fraction) of the strict IoU target

Xform2 = Callable[[np.ndarray], np.ndarray]


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def scene_to_truth_2d(m: np.ndarray) -> tuple[Xform2, float, bool]:
    """From truth->scene 4x4 `m`, a map of scene [x, z] points (n, 2) to truth
    [x, z], plus the yaw (deg) removed and whether m's 2D part is a reflection.

    Truth (x, 0, z) maps to scene x' = A @ [x, z] + b with A = m[[0, 2]][:, [0, 2]].
    A is replaced by its closest rotation, so a reflection stays visible.
    """
    a = m[np.ix_([0, 2], [0, 2])]
    b = m[[0, 2], 3]
    yaw = math.atan2(a[0, 1] - a[1, 0], a[0, 0] + a[1, 1])
    c, s = math.cos(yaw), math.sin(yaw)
    rot = np.array([[c, s], [-s, c]])  # closest rotation to a

    def to_truth(p: np.ndarray) -> np.ndarray:
        return (np.asarray(p, float).reshape(-1, 2) - b) @ rot  # rot^T (p - b), row form

    return to_truth, math.degrees(yaw), bool(np.linalg.det(a) < 0)


def _poly(ring: list, holes: list, f: Xform2) -> Polygon:
    p = Polygon(f(np.asarray(ring)), [f(np.asarray(h)) for h in holes])
    return p if p.is_valid else p.buffer(0)


def _walkable(scene: dict, f: Xform2 = lambda p: np.asarray(p, float)):
    return unary_union([_poly(p["exterior"], p.get("holes", []), f)
                        for lvl in scene["walkable"].values() for p in lvl["polygons"]])


def _centerline(poly: Polygon) -> LineString:
    """Long axis of the min rotated rectangle: segment between short-edge midpoints."""
    rect = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    edges = [(rect[i], rect[(i + 1) % 4]) for i in range(4)]
    lengths = [np.linalg.norm(q - p) for p, q in edges]
    i = int(np.argmin(lengths))
    (p0, p1), (q0, q1) = edges[i], edges[(i + 2) % 4]
    return LineString([(p0 + p1) / 2, (q0 + q1) / 2])


def _wall_error(truth: dict, pred: dict, f: Xform2) -> float | None:
    """Median distance (m) from points every 0.1 m on truth wall centerlines to the
    nearest predicted wall centerline; None if either side has no walls."""
    pred_lines = [_centerline(_poly(w["polygon"], [], f)) for w in pred["walls"]]
    if not pred_lines or not truth["walls"]:
        return None
    target = MultiLineString(pred_lines)
    pts = []
    for w in truth["walls"]:
        line = _centerline(Polygon(w["polygon"]))
        pts += [line.interpolate(d) for d in np.arange(0, line.length + 1e-9, WALL_SAMPLE_M)]
    return float(np.median(shapely.distance(np.array(pts, dtype=object), target)))


def _door_match(truth: dict, pred: dict, f: Xform2) -> int:
    """Number of one-to-one door matches with center distance <= 0.5 m."""
    tc = np.array([np.mean(d["segment"], axis=0) for d in truth["doors"]]).reshape(-1, 2)
    pc = f(np.array([np.mean(d["segment"], axis=0) for d in pred["doors"]]).reshape(-1, 2))
    if not len(tc) or not len(pc):
        return 0
    dist = np.linalg.norm(tc[:, None] - pc[None], axis=2)
    rows, cols = linear_sum_assignment(np.where(dist <= DOOR_MATCH_M, dist, 1e6))
    return int(np.sum(dist[rows, cols] <= DOOR_MATCH_M))


def _ratio(num: int, den: int) -> float:
    return num / den if den else 1.0


def score(scene: dict, truth_dir: Path) -> dict:
    """Metrics of predicted `scene` vs the truth in `truth_dir` (truth.scene.json,
    truth_transform.json). Distances in meters, angles in degrees."""
    truth = _load(Path(truth_dir) / "truth.scene.json")
    t2m = np.asarray(_load(Path(truth_dir) / "truth_transform.json")["truth_to_mesh"], float)
    i2s = np.asarray(scene["extensions"]["lidar"]["input_to_scene"], float)
    m = i2s @ t2m
    up = m[:3, :3] @ np.array([0.0, 1.0, 0.0])
    up_err = math.degrees(math.acos(np.clip(up[1] / np.linalg.norm(up), -1, 1)))
    f, yaw, mirrored = scene_to_truth_2d(m)

    tw, pw = _walkable(truth), _walkable(scene, f)
    union = tw.union(pw).area
    matched = _door_match(truth, scene, f)
    tall = [Polygon(o["polygon"]) for o in truth["obstacles"] if o["height_m"] > OBSTACLE_MIN_HEIGHT_M]
    pred_obs = [_poly(o["polygon"], [], f) for o in scene["obstacles"]]
    found = sum(any(t.intersects(p) for p in pred_obs) for t in tall)
    issues = validate(scene)
    errors = sorted({i["code"] for i in issues if i["severity"] == "error"})
    return {
        "up_error_deg": up_err,
        "yaw_deg": yaw,
        "mirrored": mirrored,
        "walkable_iou": tw.intersection(pw).area / union if union else 0.0,
        "walkable_area_truth_m2": tw.area,
        "walkable_area_pred_m2": pw.area,
        "door_matched": matched,
        "doors_truth": len(truth["doors"]),
        "doors_pred": len(scene["doors"]),
        "door_recall": _ratio(matched, len(truth["doors"])),
        "door_precision": _ratio(matched, len(scene["doors"])) if scene["doors"] else 0.0,
        "rooms_truth": len(truth["rooms"]),
        "rooms_pred": len(scene["rooms"]),
        "room_count_diff": len(scene["rooms"]) - len(truth["rooms"]),
        "wall_median_error_m": _wall_error(truth, scene, f),
        "obstacles_truth_tall": len(tall),
        "obstacles_found": found,
        "obstacle_recall": _ratio(found, len(tall)),
        "validator_errors": sum(i["severity"] == "error" for i in issues),
        "validator_error_codes": errors,
    }


# --- perfect / transformed predictions (scorer self-checks) -----------------------


def transform_scene_xz(scene: dict, a: np.ndarray, b: np.ndarray) -> dict:
    """Copy of `scene` with every 2D [x, z] point p mapped to a @ p + b (and navmesh
    vertices' x, z likewise). `a` may be a reflection (used to fake mirroring)."""
    def pt(p: list) -> list:
        q = a @ np.asarray(p, float) + b
        return [float(q[0]), float(q[1])]

    def pts(ps: list) -> list:
        return [pt(p) for p in ps]

    out = json.loads(json.dumps(scene))
    for key in ("walls", "rooms", "obstacles", "stairs"):
        for item in out.get(key, []):
            item["polygon"] = pts(item["polygon"])
    for rm in out["rooms"]:
        rm["centroid"] = pt(rm["centroid"])
    for d in out["doors"]:
        d["segment"] = pts(d["segment"])
    for e in out["exits"]:
        e["position"] = pt(e["position"])
        if e.get("assembly_point") is not None:
            e["assembly_point"] = pt(e["assembly_point"])
    for s in out.get("stairs", []):
        s["bottom"], s["top"] = pt(s["bottom"]), pt(s["top"])
    for item in out.get("points_of_interest", []) + out.get("elevators", []):
        item["position"] = pt(item["position"])
    for link in out.get("links", []):
        for end in ("from", "to"):
            link[end]["position"] = pt(link[end]["position"])
    for lvl in out["walkable"].values():
        for p in lvl["polygons"]:
            p["exterior"], p["holes"] = pts(p["exterior"]), [pts(h) for h in p.get("holes", [])]
    out["navmesh"]["vertices"] = [[q[0], v[1], q[1]] for v in out["navmesh"]["vertices"]
                                  for q in [pt([v[0], v[2]])]]
    return out


def yaw_matrix(yaw_deg: float, shift: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """4x4 rotation by yaw_deg about +Y plus an XZ shift (m); scene = this @ truth."""
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    g = np.eye(4)
    g[np.ix_([0, 2], [0, 2])] = [[c, s], [-s, c]]
    g[[0, 2], 3] = shift
    return g


def perfect_prediction(truth_dir: Path, yaw_deg: float = 30.0, shift: tuple[float, float] = (3.0, -2.0)) -> dict:
    """The truth scene as a pipeline would ideally output it: geometry moved by a
    yaw + shift G, and input_to_scene = G @ inverse(truth_to_mesh)."""
    truth = _load(Path(truth_dir) / "truth.scene.json")
    t2m = np.asarray(_load(Path(truth_dir) / "truth_transform.json")["truth_to_mesh"], float)
    g = yaw_matrix(yaw_deg, shift)
    scene = transform_scene_xz(truth, g[np.ix_([0, 2], [0, 2])], g[[0, 2], 3])
    scene["extensions"] = {"lidar": {"input_to_scene": (g @ np.linalg.inv(t2m)).tolist()}}
    return scene


# --- benchmark ------------------------------------------------------------------------


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None


def aggregate(rows: list[dict]) -> list[dict]:
    """Benchmark summary rows {metric, value, target, pass} from per-scan rows."""
    ok = [r for r in rows if "error" not in r]
    tier_a = [r for r in ok if (r["noise_sigma_m"], r["hole_frac"]) == TIER_A]
    tier_b = [r for r in ok if (r["noise_sigma_m"], r["hole_frac"]) != TIER_A]
    frac = lambda pred, rs: sum(map(pred, rs)) / len(rs) if rs else None  # noqa: E731
    dm = sum(r["door_matched"] for r in ok)
    walls = _mean([r["wall_median_error_m"] for r in ok])
    out = [
        ("scans completed", len(ok) / len(rows) if rows else None, "== 1.0", lambda v: v == 1.0),
        ("up error < 2 deg (fraction)", frac(lambda r: r["up_error_deg"] < 2, ok), "== 1.0", lambda v: v == 1.0),
        ("mirrored (fraction)", frac(lambda r: r["mirrored"], ok), "== 0.0", lambda v: v == 0.0),
        ("walkable IoU mean, 1.5 cm / 5%", _mean([r["walkable_iou"] for r in tier_a]), ">= 0.90", lambda v: v >= 0.90),
        ("walkable IoU mean, 3 cm / 10%", _mean([r["walkable_iou"] for r in tier_b]), ">= 0.80", lambda v: v >= 0.80),
        ("door recall (pooled)", _ratio(dm, sum(r["doors_truth"] for r in ok)) if ok else None, ">= 0.85", lambda v: v >= 0.85),
        ("door precision (pooled)", _ratio(dm, sum(r["doors_pred"] for r in ok)) if ok else None, ">= 0.80", lambda v: v >= 0.80),
        ("room count within +-1 (fraction)", frac(lambda r: abs(r["room_count_diff"]) <= 1, ok), ">= 0.85", lambda v: v >= 0.85),
        ("wall centerline median error m (mean)", walls, "< 0.10", lambda v: v < 0.10),
        ("obstacle recall > 0.5 m (pooled)",
         _ratio(sum(r["obstacles_found"] for r in ok), sum(r["obstacles_truth_tall"] for r in ok)) if ok else None,
         ">= 0.80", lambda v: v >= 0.80),
        ("validator-clean scenes (fraction)", frac(lambda r: r["validator_errors"] == 0, ok), "== 1.0", lambda v: v == 1.0),
        ("max runtime s", max((r["runtime_s"] for r in ok), default=None), "<= 90 (500k tris)", lambda v: v <= 90),
    ]
    return [{"metric": n, "value": v, "target": t, "pass": None if v is None else bool(chk(v))} for n, v, t, chk in out]


def _markdown(summary: list[dict], rows: list[dict]) -> str:
    fmt = lambda v: "-" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)  # noqa: E731
    lines = ["# Synthetic benchmark", "", "| metric | value | target | pass |", "|---|---|---|---|"]
    lines += [f"| {s['metric']} | {fmt(s['value'])} | {s['target']} | {fmt(s['pass'])} |" for s in summary]
    cols = ["scan", "up_error_deg", "walkable_iou", "door_recall", "door_precision", "room_count_diff",
            "wall_median_error_m", "obstacle_recall", "validator_errors", "runtime_s"]
    lines += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['scan']} | FAILED: {r['error']} |")
        else:
            lines.append("| " + " | ".join(fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def run_bench(bench: Path, out: Path, perfect: bool = False) -> list[dict]:
    """Run the pipeline (or the perfect prediction) on every scan dir under `bench`,
    score it, and write out/report.json and out/report.md. Returns the summary."""
    if not perfect:
        from ingest_lidar.pipeline import run
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for scan_dir in sorted(p for p in Path(bench).iterdir() if (p / "truth.scene.json").exists()):
        params = _load(scan_dir / "scan_params.json")
        row = {"scan": scan_dir.name, **{k: params[k] for k in ("noise_sigma_m", "hole_frac", "drift_m", "triangles")}}
        start = time.perf_counter()
        try:
            scene = perfect_prediction(scan_dir) if perfect else run(scan_dir / "scan.obj", out / scan_dir.name, no_llm=True)
            row["runtime_s"] = time.perf_counter() - start
            row.update(score(scene, scan_dir))
        except Exception as exc:  # one broken scan must not stop the benchmark
            row.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        rows.append(row)
        print(f"{scan_dir.name}: " + (row.get("error") or
              f"IoU {row['walkable_iou']:.3f} doors {row['door_recall']:.2f}/{row['door_precision']:.2f} "
              f"rooms {row['room_count_diff']:+d} up {row['up_error_deg']:.2f} {row['runtime_s']:.1f} s"))
    summary = aggregate(rows)
    (out / "report.json").write_text(json.dumps({"summary": summary, "scans": rows}, indent=1) + "\n")
    md = _markdown(summary, rows)
    (out / "report.md").write_text(md)
    print(md)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", type=Path, required=True, help="dir of scan dirs from make_synthetic_scan --bench")
    ap.add_argument("-o", "--out", type=Path, default=Path("out/bench"))
    ap.add_argument("--perfect", action="store_true", help="score the transformed truth instead of the pipeline")
    args = ap.parse_args(argv)
    summary = run_bench(args.bench, args.out, args.perfect)
    return 0 if all(s["pass"] is not False for s in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
