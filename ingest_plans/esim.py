"""Multi-level contract scene from CMU-style ESIM CAD floor plan PDFs.

Inputs per floor: <BLDG>-<floor>-ESIM-Base.pdf (CAD layers kept as PDF optional content:
A-AREA room boundaries, ARCH|A-DOOR door symbols, ARCH|A-FLOR-STRS stair treads, A-SPAC-EXTR
gross outline, room number and "<n>sf" labels) and optionally <BLDG>-<floor>-ESIM-Type.pdf
(rooms hatched by FICM space type) plus TypeChart sheets with the color key (when there is no
chart, the key printed on the Type sheets is used).

Per floor: rooms = polygonized A-AREA linework holding a room-number label; scale = median of
sqrt(labeled sf * 0.092903 / traced area); type = majority hatch color under the room; doors =
A-DOOR symbols placed on the wall strip between the two spaces they touch; walls = gross outline
minus rooms minus door openings. Floors are aligned on vertical cores, stacked at the configured
floor height, and joined by stairs where stair rooms overlap on consecutive floors. Exits are
exterior doors at grade only. Rooms with no path to an exit (doors, stairs going down) are left
out of `rooms` and listed in extensions.<key>.unreachable, so the scene validates.

Building specifics (floor order, grade floors, core room numbers, color overrides) come from a
small JSON config; see ingest_plans/configs/ and ingest_plans/README.md.
"""

from __future__ import annotations

import collections
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import fitz
import networkx as nx
import numpy as np
import shapely
import shapely.affinity
import shapely.ops
import trimesh
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polygonize, split, unary_union

from ingest_lidar.geometry import (door_gap_box, extrude_xz, navmesh_from_walkable, ring, walkable_from_parts,
                                   walkable_to_contract)
from tools.layouts import _round

SQFT_M2 = 0.092903
R = 0.25
SF = re.compile(r"^([\d,]+)\s*sf$", re.I)
SHELL_CLOSE_M = 0.6  # closes walls up to 1.2 m thick when the outline comes from the rooms
TREAD_MIN_M = 8.0  # tread linework inside a room that makes it a stairwell (auto stair detection)

# FICM code -> contract room type. Codes not listed map to "unknown". Residential sleeping rooms
# (9xx) have no contract type; they stay "unknown" with their FICM code in extensions.
FICM_TYPE = {"110": "classroom", "115": "storage", "210": "classroom", "215": "storage", "220": "classroom",
             "225": "storage", "250": "office", "310": "office", "310.1": "office", "315": "storage",
             "315.1": "storage", "350": "office", "455": "storage", "520": "unknown", "525": "storage",
             "610": "lobby", "615": "storage", "615.1": "storage", "630": "lobby", "635": "storage",
             "650": "lobby", "655": "storage", "680": "office", "685": "storage", "710": "mechanical",
             "730": "storage", "880": "lobby", "935": "storage", "W02": "unknown", "W06": "corridor",
             "W07": "stairwell", "X02": "storage", "X03": "restroom", "X03.3": "restroom", "Y04": "mechanical",
             "070.1": "unknown"}


@dataclass
class Config:
    """Building settings. Defaults suit any ESIM sheet set; configs/*.json override them."""

    name: str = "building"
    ext_key: str = "esim"
    floors: list[str] | str = "auto"  # bottom to top, or "auto" (see floor_order)
    floor_order: str = "numeric"  # "numeric": numbers ascending, others before them alphabetically;
    #                               "cmu": C < B < A < 1 < 2 ... (letters are basements)
    ground_floor: str | None = None  # elevation 0; default the first numbered floor, else the lowest
    floor_height_m: float = 4.0
    ceiling_m: float = 3.0
    grade_floors: list[str] | None = None  # floors whose exterior doors may be exits; None = all
    room_number: str = r"^([A-Z]{0,3}\d{2,4}[A-Z]?|[A-Z]{3}\d-\d+[A-Z]?)$"
    skip_prefixes: list[str] = field(default_factory=lambda: ["VPN"])
    open_to_below_rgb: list[float] | None = None
    color_types: dict[str, str] = field(default_factory=dict)  # "r,g,b" -> contract type
    big_room_retype: dict[str, list] = field(default_factory=dict)  # type -> [min m^2, new type]
    stair_suffixes: list[str] | None = None  # room number[1:4]; None = detect stairs by treads
    elevator_suffixes: list[str] = field(default_factory=list)
    align_suffixes: list[str] | None = None  # None = auto (room numbers that repeat, then stairs)
    source_note: str = "ESIM floor plan PDFs (not redistributed)"

    @property
    def room_re(self) -> re.Pattern:
        return re.compile(self.room_number)

    @property
    def open_rgb(self) -> tuple | None:
        return tuple(self.open_to_below_rgb) if self.open_to_below_rgb else None


def _rgb(c) -> tuple:
    return tuple(round(x, 2) for x in c)


def _lines(d: dict) -> list[LineString]:
    out = []
    for it in d["items"]:
        if it[0] == "l":
            out.append(LineString([(it[1].x, it[1].y), (it[2].x, it[2].y)]))
        elif it[0] == "c":
            p = [np.array([q.x, q.y]) for q in it[1:5]]
            t = np.linspace(0, 1, 9)[:, None]
            pts = (1 - t) ** 3 * p[0] + 3 * (1 - t) ** 2 * t * p[1] + 3 * (1 - t) * t ** 2 * p[2] + t ** 3 * p[3]
            out.append(LineString(pts))
        elif it[0] == "re":
            r = it[1]
            out.append(LineString([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)]))
        elif it[0] == "qu":
            q = it[1]
            out.append(LineString([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y), (q.ul.x, q.ul.y)]))
    return out


def legend(chart_pdfs: list[Path]) -> dict[tuple, list[str]]:
    """Hatch color -> FICM codes, from the swatch beside each code line on the type charts (or on
    the Type sheets themselves, which print the same key)."""
    out: dict[tuple, set] = collections.defaultdict(set)
    for pdf in chart_pdfs:
        page = fitz.open(pdf)[0]
        codes = [w for w in page.get_text("words") if re.fullmatch(r"[0-9WXY]\d{2}(\.\d)?", w[4])]
        for d in page.get_drawings():
            r = d["rect"]
            if not d.get("fill") or r.width > 60 or r.width < 3 or d.get("layer") == "A-SPAC-PATT":
                continue
            cy = (r.y0 + r.y1) / 2
            # The code sits just right of its swatch on the same row (the chart has two columns).
            near = [w[4] for w in codes if w[1] - 4 <= cy <= w[3] + 4 and r.x1 <= w[0] <= r.x1 + 60]
            if near:
                out[_rgb(d["fill"])].add(near[0])
    return {k: sorted(v) for k, v in out.items()}


def _labels(page: fitz.Page, leaders: list[LineString], room_re: re.Pattern) -> list[dict]:
    """Room number + sf pairs with the point that identifies their room (leader end when present)."""
    words = page.get_text("words")
    sfs = [w for w in words if SF.match(w[4])]
    out = []
    for w in words:
        if not room_re.match(w[4]) or SF.match(w[4]):
            continue
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        below = [s for s in sfs if 0 < s[1] - w[1] < 10 and abs((s[0] + s[2]) / 2 - cx) < 12]
        sf = float(SF.match(below[0][4]).group(1).replace(",", "")) if below else None
        out.append({"number": w[4], "at": Point(cx, cy), "bbox": box(w[0], w[1], w[2], w[3]), "sf": sf})
    return out


def _shell(gross: list[Polygon], faces: list[Polygon], m_per_pt: float) -> Polygon:
    """Building outline in PDF units: the largest gross-area polygon when the rooms fill most of
    it, else the rooms closed over their walls. Some sheets draw the gross line open (no polygon)
    or around a courtyard; a hull would turn the courtyard into floor."""
    rooms = unary_union(faces)
    full = [p for p in gross if p.intersection(rooms).area >= 0.6 * p.area]
    if full:
        return max(full, key=lambda p: p.area)
    c = SHELL_CLOSE_M / m_per_pt
    closed = rooms.buffer(c, join_style="mitre").buffer(-c, join_style="mitre")
    parts = [Polygon(p.exterior) for p in getattr(closed, "geoms", [closed]) if p.area > 0.02 * closed.area]
    return unary_union(parts)


def read_floor(base_pdf: Path, type_pdf: Path | None, colors: dict, cfg: Config) -> dict:
    page = fitz.open(base_pdf)[0]
    drawings = page.get_drawings()
    by_layer: dict[str, list] = collections.defaultdict(list)
    for d in drawings:
        by_layer[d.get("layer") or ""].append(d)
    area_lines = [ln for d in by_layer["A-AREA"] for ln in _lines(d)]
    faces = [f for f in polygonize(unary_union(area_lines)) if f.area > 1.0]
    leaders = [ln for d in by_layer["A-SPAC-IDEN-LEDR"] for ln in _lines(d)]
    ext_lines = [ln for d in by_layer["A-SPAC-EXTR"] for ln in _lines(d)]
    # Per path and all paths together: one building draws its outline as one path, another as many.
    gross = [p for d in by_layer["A-SPAC-EXTR"] for p in polygonize(unary_union(_lines(d)))]
    gross += list(polygonize(unary_union(ext_lines))) if ext_lines else []

    rooms: dict[str, dict] = {}
    for lab in _labels(page, leaders, cfg.room_re):
        hit = [f for f in faces if f.contains(lab["at"])]
        if not hit:  # label outside its room: follow the leader that starts at the label
            for ln in leaders:
                a, b = Point(ln.coords[0]), Point(ln.coords[-1])
                if lab["bbox"].buffer(3).intersects(a) or lab["bbox"].buffer(3).intersects(b):
                    far = b if lab["bbox"].buffer(3).intersects(a) else a
                    hit = [f for f in faces if f.buffer(0.5).contains(far)]
                    if hit:
                        break
        if hit:
            face = min(hit, key=lambda f: f.area)
            if lab["number"] not in rooms and not any(r["poly"].equals(face) for r in rooms.values()):
                rooms[lab["number"]] = {"poly": face, "sf": lab["sf"]}

    ratios = [np.sqrt(r["sf"] * SQFT_M2 / r["poly"].area) for r in rooms.values() if r["sf"] and r["sf"] >= 50]
    m_per_pt = float(np.median(ratios))
    shell = _shell(gross, faces, m_per_pt)

    # Type: majority hatch color inside each room on the Type sheet.
    fills = [(box(*d["rect"]), _rgb(d["fill"])) for d in (fitz.open(type_pdf)[0].get_drawings() if type_pdf else [])
             if d.get("fill") and d.get("layer") == "A-SPAC-PATT"]
    tree = shapely.STRtree([p for p, _ in fills])
    for r in rooms.values():
        inner = r["poly"].buffer(-0.3)
        votes = collections.Counter()
        for i in tree.query(inner, predicate="intersects"):
            votes[fills[i][1]] += fills[i][0].intersection(inner).area
        color = votes.most_common(1)[0][0] if votes else None
        r["ficm"] = colors.get(color, []) if color else []
        r["color"] = color

    doors = []
    for d in by_layer["ARCH|A-DOOR"]:
        g = unary_union(_lines(d))
        if not g.is_empty:
            doors.append(g)
    # One door symbol is often several paths: merge the ones that touch.
    merged = unary_union([g.buffer(0.6) for g in doors])
    symbols = [unary_union([g for g in doors if g.intersects(part)]) for part in getattr(merged, "geoms", [merged])]
    stairs_layer = unary_union([ln for d in by_layer["ARCH|A-FLOR-STRS"] for ln in _lines(d)])
    for r in rooms.values():
        r["tread_m"] = r["poly"].intersection(stairs_layer).length  # PDF units; scaled by the caller
    used = [r["poly"] for r in rooms.values()]
    others = [f for f in faces if not any(f.equals(u) for u in used)]
    return {"rooms": rooms, "unlabeled": others, "shell": shell, "doors": symbols, "m_per_pt": m_per_pt,
            "scale_iqr": [float(np.percentile(ratios, 25)), float(np.percentile(ratios, 75))], "n_scale": len(ratios),
            "treads": stairs_layer}


WALL_REACH_M = 0.6  # widest wall a door symbol may cross
DOOR_W = (0.8, 2.0)
EXTERIOR_WALL_M = 0.3  # A-SPAC-EXTR is the outer face of the exterior wall
CORRIDOR_MIN_M = 0.9
INFERRED_DOOR_M = 0.9
INFER_REACH_M = 1.2  # inferred doors may cross thicker walls (glazed suite fronts)
GAP_OVERLAP_M = 0.8  # door boxes extend 0.4 m into each room so off-center symbols still bridge
OPEN_GAP_M = 0.08  # spaces closer than this have no wall between them


def color_type(color: tuple | None, colors: dict, cfg: Config) -> str:
    """Contract type for a hatch color: config override, else the FICM codes the key gives it
    when they all agree, else "unknown"."""
    if color is None:
        return "unknown"
    key = ",".join(str(c) for c in color)
    if key in cfg.color_types:
        return cfg.color_types[key]
    types = {FICM_TYPE.get(c, "unknown") for c in colors.get(color, [])}
    return types.pop() if len(types) == 1 else "unknown"


def _suffix(number: str) -> str:
    return number[1:4]


def is_stair(number: str, r: dict, s: float, cfg: Config) -> bool:
    if cfg.stair_suffixes is not None:
        return _suffix(number) in cfg.stair_suffixes
    return r.get("tread_m", 0.0) * s >= TREAD_MIN_M


def room_type(number: str, r: dict, s: float, colors: dict, cfg: Config) -> str:
    if is_stair(number, r, s, cfg):
        return "stairwell"
    if _suffix(number) in cfg.elevator_suffixes:
        return "unknown"
    t = color_type(r["color"], colors, cfg)
    big = cfg.big_room_retype.get(t)
    if big and r["poly"].area * s * s > big[0]:
        return big[1]
    return t


def _floor_key(number: str, floor: str) -> str | None:
    """Room number with its floor digit taken out (2611 -> 611 on floor 2, C205A -> C05A on 2),
    so the same position on another floor has the same key; None when the floor id is absent."""
    i = number.find(floor)
    return number[:i] + number[i + 1:] if i >= 0 and len(number) > 2 else None


def _shift_by_keys(base: dict, fd: dict, bf: str, f: str, keys: list[str] | None) -> np.ndarray | None:
    shifts = []
    for num, r in fd["rooms"].items():
        k = _suffix(num) if keys is not None else _floor_key(num, f)
        if k is None or (keys is not None and (k not in keys or not num.startswith(f))):
            continue
        twin = (bf + k) if keys is not None else next((n for n in base["rooms"] if _floor_key(n, bf) == k), None)
        a = base["rooms"].get(twin) if twin else None
        if not a or (keys is None and not (a["sf"] and r["sf"] and abs(a["sf"] - r["sf"]) <= 0.15 * a["sf"])):
            continue
        ca, cb = a["poly"].centroid, r["poly"].centroid
        shifts.append((ca.x * base["m_per_pt"] - cb.x * fd["m_per_pt"], ca.y * base["m_per_pt"] - cb.y * fd["m_per_pt"]))
    if keys is not None:
        return np.median(np.array(shifts), axis=0) if shifts else None
    if len(shifts) < 3:
        return None
    arr = np.array(shifts)
    med = np.median(arr, axis=0)
    inl = arr[np.linalg.norm(arr - med, axis=1) < 0.5]
    return np.median(inl, axis=0) if len(inl) >= 3 else None


def _shift_by_stairs(base: dict, fd: dict, cfg: Config) -> np.ndarray | None:
    """Translation that lands the most stair rooms of `fd` on stair rooms of `base`."""
    def stairs(d):
        return [np.array(r["poly"].centroid.coords[0]) * d["m_per_pt"]
                for n, r in d["rooms"].items() if is_stair(n, r, d["m_per_pt"], cfg)]
    sa, sb = stairs(base), stairs(fd)
    best = None
    for a in sa:
        for b in sb:
            t = a - b
            hits = [min(np.linalg.norm(x + t - y) for y in sa) for x in sb]
            score = (sum(h < 1.5 for h in hits), -sum(h for h in hits if h < 1.5))
            if best is None or score > best[0]:
                best = (score, t)
    return best[1] if best and best[0][0] >= 2 else None


def _align(floors: dict, order: list[str], cfg: Config) -> dict[str, tuple[float, float, float]]:
    """Per floor (scale m/pt, dx, dz). Sheets are each fit to the page, so scale and origin
    differ; the translation lands each floor's vertical cores on the lowest floor's: configured
    core rooms (align_suffixes), else room numbers that repeat floor to floor with similar area,
    else stair rooms. Floors chain through their neighbor when the lowest floor shares no keys."""
    out = {order[0]: (floors[order[0]]["m_per_pt"], 0.0, 0.0)}
    for i, f in enumerate(order[1:], 1):
        fd = floors[f]
        shift = None
        for bf in [order[0]] + order[1:i][::-1]:
            shift = _shift_by_keys(floors[bf], fd, bf, f, cfg.align_suffixes)
            if shift is None and cfg.align_suffixes is None:
                shift = _shift_by_stairs(floors[bf], fd, cfg)
            if shift is not None:
                shift = shift + np.array(out[bf][1:])
                break
        if shift is None:
            raise ValueError(f"floor {f}: no shared cores to align it")
        out[f] = (fd["m_per_pt"], float(shift[0]), float(shift[1]))
    return out


def _tf(geom, t: tuple[float, float, float]):
    s, dx, dz = t
    return shapely.affinity.affine_transform(geom, [s, 0, 0, s, dx, dz])


def place_doors(symbols: list, spaces: dict[str, Polygon], outside: Polygon) -> list[dict]:
    """Door segments (meters) from door symbols: the opening is where the symbol meets the wall
    strip between the two spaces it touches; 'outside' when one side is the exterior."""
    tree_ids = list(spaces)
    tree = shapely.STRtree([spaces[k] for k in tree_ids])
    doors = []
    for g in symbols:
        hull = g.convex_hull.buffer(0.15)
        near = [tree_ids[i] for i in tree.query(hull.buffer(WALL_REACH_M))]
        cands = [(k, spaces[k]) for k in near] + ([("outside", outside)] if outside.distance(hull) < WALL_REACH_M else [])
        best = None
        for i, (a, pa) in enumerate(cands):
            for b, pb in cands[i + 1:]:
                if pa.distance(pb) > WALL_REACH_M:
                    continue
                strip = pa.buffer(WALL_REACH_M).intersection(pb.buffer(WALL_REACH_M)).difference(pa).difference(pb)
                hit = strip.intersection(hull)
                if not hit.is_empty and (best is None or hit.area > best[0]):
                    best = (hit.area, a, b, pa, pb, strip)
        if best is None:
            continue
        _, a, b, pa, pb, strip = best
        c = strip.intersection(hull).centroid
        # Wall direction: perpendicular to the shortest hop between the two faces near the door.
        qa = shapely.ops.nearest_points(pa.boundary, c)[0]
        qb = shapely.ops.nearest_points(pb.boundary, c)[0]
        n = np.array([qb.x - qa.x, qb.y - qa.y])
        thick = float(np.linalg.norm(n))
        if thick < 0.02:
            continue
        n /= thick
        u = np.array([-n[1], n[0]])
        mid = (np.array(qa.coords[0]) + np.array(qb.coords[0])) / 2
        proj = (np.asarray(g.convex_hull.exterior.coords) - mid) @ u if g.convex_hull.geom_type == "Polygon" else np.array([0.0])
        width = float(np.clip(proj.max() - proj.min(), *DOOR_W))
        center = mid + u * (proj.max() + proj.min()) / 2
        seg = [(center - u * width / 2).tolist(), (center + u * width / 2).tolist()]
        doors.append({"segment": seg, "width": width, "connects": [a, b], "thick": max(thick, 0.1)})
    # Symbols of one double door can both land on the same opening: keep one per opening.
    kept = []
    for d in doors:
        m = np.mean(d["segment"], axis=0)
        if all(np.linalg.norm(m - np.mean(k["segment"], axis=0)) > 0.5 or set(k["connects"]) != set(d["connects"]) for k in kept):
            kept.append(d)
    return kept


def open_connections(spaces: dict[str, Polygon], doors: list[dict]) -> list[dict]:
    """Openings between spaces that touch with no wall between them (area lines that split one
    open floor, e.g. corridor segments). One opening per pair, centered on the shared edge,
    at most 3 m wide (the contract's door width limit)."""
    ids = list(spaces)
    tree = shapely.STRtree([spaces[k] for k in ids])
    have = {frozenset(d["connects"]) for d in doors}
    out = []
    for i, a in enumerate(ids):
        for j in tree.query(spaces[a].buffer(OPEN_GAP_M)):
            b = ids[j]
            if j <= i or frozenset((a, b)) in have:
                continue
            shared = spaces[a].buffer(OPEN_GAP_M).intersection(spaces[b].buffer(OPEN_GAP_M))
            if shared.is_empty or shared.area / (2 * OPEN_GAP_M) < 0.8:
                continue
            seam = shared  # the whole wall-less strip; it bridges the walkable, not just the door box
            shared = max(getattr(shared, "geoms", [shared]), key=lambda p: p.area)
            rect = shared.minimum_rotated_rectangle
            c = np.asarray(rect.exterior.coords)[:4]
            e1, e2 = c[1] - c[0], c[2] - c[1]
            u = e1 if np.linalg.norm(e1) >= np.linalg.norm(e2) else e2
            length = float(np.linalg.norm(u))
            u = u / length
            mid = np.asarray(shared.centroid.coords[0])
            width = min(length, 3.0)
            seg = [(mid - u * width / 2).tolist(), (mid + u * width / 2).tolist()]
            out.append({"segment": seg, "width": width, "connects": [a, b], "thick": 0.1, "kind": "opening",
                        "shared_m": length, "bridge": seam})
            have.add(frozenset((a, b)))
    return out


def _outside_probe(door: dict, shell: Polygon, dist: float = 1.5) -> Point:
    """Point `dist` m out from an exterior door's midpoint, on the side away from the building."""
    p0, p1 = np.asarray(door["segment"], float)
    u = (p1 - p0) / np.linalg.norm(p1 - p0)
    n, mid = np.array([-u[1], u[0]]), (p0 + p1) / 2
    a, b = Point(mid + n * dist), Point(mid - n * dist)
    return a if shell.distance(a) > shell.distance(b) or not shell.contains(a) and shell.contains(b) else b


def _reach(per: dict, pairs: list, below_ground: set[str] = frozenset()) -> set:
    """(floor, space) nodes with a path to outside through doors (either way) and stairs taken
    downward only: evacuees do not climb to get out, except from basements (below_ground), which
    climb toward the ground floor. pairs: (lo, room_lo, hi, room_hi)."""
    g = nx.DiGraph()
    g.add_node("outside")
    for f, pf in per.items():
        g.add_nodes_from((f, r) for r in pf["spaces"])
        for d in pf["doors"]:
            a, b = ((f, x) if x != "outside" else "outside" for x in d["connects"])
            g.add_edges_from([(a, b), (b, a)])
    for lo, rlo, hi, rhi in pairs:
        g.add_edge((hi, rhi), (lo, rlo))
        if lo in below_ground:
            g.add_edge((lo, rlo), (hi, rhi))
    return nx.ancestors(g, "outside") | {"outside"}


def fill_stair_cores(per: dict, order: list[str], suffixes: list[str]) -> None:
    """Give every floor between a stair core's lowest and highest appearance its core room.

    Stair shafts are vertical, but a floor's core label sometimes fails to match its A-AREA face
    (e.g. 2903, 3903, 4902), which left floors without a way down. Take the core outline from the
    nearest floor that has it; reuse the space there if one covers it, else add it as a room.
    """
    for suf in suffixes:
        have = [f for f in order if f"r_{f}{suf}" in per[f]["spaces"]]
        if len(have) < 2:
            continue
        for f in order[order.index(have[0]):order.index(have[-1]) + 1]:
            rid = f"r_{f}{suf}"
            if rid in per[f]["spaces"]:
                continue
            src = min(have, key=lambda h: abs(order.index(h) - order.index(f)))
            core = per[src]["spaces"][f"r_{src}{suf}"]
            spaces, info = per[f]["spaces"], per[f]["info"]
            over = [(spaces[k].intersection(core).area, k) for k in spaces if spaces[k].intersects(core)]
            area, k = max(over) if over else (0.0, None)
            if k and area > 0.5 * core.area and spaces[k].area < 2 * core.area and k[0] in "cu":
                spaces[rid], info[rid] = spaces.pop(k), {**info.pop(k), "number": f"{f}{suf}", "type": "stairwell",
                                                          "inferred": f"stair core copied from floor {src}"}
                per[f]["doors"] = [{**d, "connects": [rid if c == k else c for c in d["connects"]]} for d in per[f]["doors"]]
                continue
            others = unary_union([spaces[x] for x in spaces if spaces[x].intersects(core)]) if over else Polygon()
            piece = core.difference(others.buffer(0.05))
            piece = max(getattr(piece, "geoms", [piece]), key=lambda p: p.area) if not piece.is_empty else piece
            if piece.is_empty or piece.area < 0.5 * core.area:
                continue
            spaces[rid] = piece
            info[rid] = {"number": f"{f}{suf}", "type": "stairwell", "sf": None, "ficm": [], "color": None,
                         "inferred": f"stair core copied from floor {src}"}


def infer_door(rid: str, pf: dict, reachable: set[str]) -> dict | None:
    """A 0.9 m door on the longest wall `rid` shares (>= 0.8 m, wall <= INFER_REACH_M) with a
    reachable space; corridors and lobbies win over other rooms. None when nothing qualifies."""
    a = pf["spaces"][rid]
    best = None
    for other in sorted(reachable):  # sorted: set order follows the hash seed
        b = pf["spaces"][other]
        if a.distance(b) > INFER_REACH_M:
            continue
        # Only the edges that face `b` across the wall: within the actual gap plus 10 cm.
        shared = a.boundary.intersection(b.buffer(a.distance(b) + 0.1))
        pieces = [ln for ln in getattr(shared, "geoms", [shared]) if ln.geom_type == "LineString"]
        if not pieces:
            continue
        ln = max(pieces, key=lambda x: x.length)
        if ln.length < 0.8:
            continue
        rank = (pf["info"][other]["type"] in ("corridor", "lobby"), ln.length)
        if best is None or rank > best[0]:
            best = (rank, other, ln, b)
    if best is None:
        return None
    _, other, ln, b = best
    c = ln.interpolate(0.5, normalized=True)
    q = shapely.ops.nearest_points(b.boundary, c)[0]
    near = np.asarray(c.coords[0])
    # Direction of the local wall: the shared line's segment that holds the midpoint.
    pts = np.asarray(ln.coords)
    k = min(range(len(pts) - 1), key=lambda i: LineString(pts[i:i + 2]).distance(c))
    u = (pts[k + 1] - pts[k]) / np.linalg.norm(pts[k + 1] - pts[k])
    mid = (near + np.asarray(q.coords[0])) / 2
    w = INFERRED_DOOR_M
    return {"segment": [(mid - u * w / 2).tolist(), (mid + u * w / 2).tolist()], "width": w, "connects": [rid, other],
            "thick": max(c.distance(q), 0.1), "kind": "swing", "inferred": "no door symbol matched; longest shared wall"}


def merge_open_circulation(spaces: dict, info: dict, doors: list) -> tuple[dict, dict, list]:
    """Union unnumbered circulation pieces (c_*, u_*) that meet with no wall: one open space split
    by area lines. Numbered rooms keep their identity and stay joined by "opening" doors (merging
    them on shared length over-merged offices behind thin glazing into corridors).
    The merged space keeps the largest member's id; all members go to extensions."""
    g = nx.Graph()
    for d in doors:
        a, b = d["connects"]
        if d.get("kind") == "opening" and a[0] in "cu" and b[0] in "cu":
            g.add_edge(a, b)
    rename = {}
    for comp in nx.connected_components(g):
        keep = max(sorted(comp), key=lambda r: spaces[r].area)
        poly = unary_union([spaces[r].buffer(OPEN_GAP_M, join_style="mitre") for r in comp]).buffer(-OPEN_GAP_M, join_style="mitre")
        spaces = {**{k: v for k, v in spaces.items() if k not in comp},
                  keep: max(getattr(poly, "geoms", [poly]), key=lambda p: p.area)}
        members = sorted(info[r]["number"] for r in comp)
        typ = "corridor" if all(r[0] in "cu" for r in comp) else info[keep]["type"]
        info = {**{k: v for k, v in info.items() if k not in comp}, keep: {**info[keep], "type": typ, "members": members}}
        rename.update({r: keep for r in comp})
    out, seen = [], set()
    for d in doors:
        c = [rename.get(x, x) for x in d["connects"]]
        if c[0] == c[1]:
            continue
        key = (frozenset(c), d.get("kind"), tuple(np.round(np.mean(d["segment"], axis=0), 1)))
        if key not in seen:
            seen.add(key)
            out.append({**d, "connects": c})
    return spaces, info, out


def _stair_pairs(per: dict, lo: str, hi: str, cfg: Config) -> list[tuple[str, str, str]]:
    """(key, room on lo, room on hi) for each stair that continues from lo to hi: configured core
    numbers, or any stair rooms overlapping by 30% of the smaller one."""
    out = []
    if cfg.stair_suffixes:
        for suf in cfg.stair_suffixes:
            a, b = per[lo]["spaces"].get(f"r_{lo}{suf}"), per[hi]["spaces"].get(f"r_{hi}{suf}")
            if a is not None and b is not None and a.intersection(b).area >= 0.3 * min(a.area, b.area):
                out.append((suf, f"r_{lo}{suf}", f"r_{hi}{suf}"))
        return out
    lows = [r for r, i in per[lo]["info"].items() if i.get("stair")]
    highs = [r for r, i in per[hi]["info"].items() if i.get("stair")]
    for rl in lows:
        a = per[lo]["spaces"][rl]
        best = max(((a.intersection(per[hi]["spaces"][rh]).area, rh) for rh in highs), default=(0.0, None))
        if best[1] and best[0] >= 0.3 * min(a.area, per[hi]["spaces"][best[1]].area):
            out.append((per[lo]["info"][rl]["number"], rl, best[1]))
    return out


def _hole_free(geom) -> list[Polygon]:
    """Split polygons with holes by vertical cuts through each hole until none remain."""
    out, todo = [], [p for p in getattr(geom, "geoms", [geom]) if isinstance(p, Polygon) and not p.is_empty]
    while todo:
        p = todo.pop()
        if not p.interiors:
            out.append(p)
            continue
        x = Polygon(p.interiors[0]).centroid.x
        line = LineString([(x, p.bounds[1] - 1), (x, p.bounds[3] + 1)])
        todo += [q for q in split(p, line).geoms if isinstance(q, Polygon) and q.area > 1e-4]
    return out


def build(sheets: dict[str, dict], charts: list[Path], cfg: Config, generated_by: str = "ingest_plans") -> tuple[dict, dict]:
    """sheets: floor id -> {"base": Path, "type": Path | None}, already in bottom-to-top order.
    charts: PDFs carrying the hatch color key (TypeChart sheets, else the Type sheets)."""
    FLOORS = list(sheets)
    colors = legend(charts)
    floors = {f: read_floor(sh["base"], sh.get("type"), colors, cfg) for f, sh in sheets.items()}
    align = _align(floors, FLOORS, cfg)
    lid = {f: f"L{f}" for f in FLOORS}
    ground = cfg.ground_floor if cfg.ground_floor in FLOORS else next((f for f in FLOORS if f.isdigit()), FLOORS[0])
    elev = {f: (i - FLOORS.index(ground)) * cfg.floor_height_m for i, f in enumerate(FLOORS)}
    below_ground = set(FLOORS[:FLOORS.index(ground)])
    grade = set(cfg.grade_floors) if cfg.grade_floors is not None else set(FLOORS)
    ext = cfg.ext_key
    stats: dict = {"floors": {}}
    per: dict[str, dict] = {}
    for f, fd in floors.items():
        t = align[f]
        spaces, info = {}, {}
        for num, r in fd["rooms"].items():
            if (cfg.open_rgb and r["color"] == cfg.open_rgb) or any(num.startswith(x) for x in cfg.skip_prefixes):
                continue  # atrium voids and shafts are not floor
            rid = f"r_{num}"
            spaces[rid] = _tf(r["poly"], t)
            info[rid] = {"number": num, "type": room_type(num, r, t[0], colors, cfg), "sf": r["sf"],
                         "ficm": r["ficm"], "color": r["color"], "stair": is_stair(num, r, t[0], cfg)}
        # A-AREA faces with no label are mostly circulation (corridors are often unnumbered).
        for i, poly in enumerate(sorted(fd["unlabeled"], key=lambda p: (p.bounds[0], p.bounds[1])), 1):
            pm = _tf(poly, t)
            if pm.area < 2.0:
                continue
            rid = f"u_{f}_{i:03d}"
            spaces[rid] = pm
            info[rid] = {"number": f"{f}-U{i:03d}", "type": "corridor" if pm.area > 15 else "unknown",
                         "sf": None, "ficm": [], "color": None}
        shell = _tf(fd["shell"], t)
        faces_m = unary_union([_tf(r["poly"], t) for r in fd["rooms"].values()] + [_tf(q, t) for q in fd["unlabeled"]])
        # Corridors are mostly not A-AREA faces: they are the space left inside the outline once
        # rooms are cut out. An opening of CORRIDOR_MIN_M drops the wall strips between rooms.
        rest = shell.buffer(-EXTERIOR_WALL_M).difference(faces_m.buffer(0.02))
        h = CORRIDOR_MIN_M / 2
        opened = rest.buffer(-h, join_style="mitre").buffer(h, join_style="mitre").intersection(rest)
        for i, pm in enumerate(sorted((p for p in getattr(opened, "geoms", [opened]) if isinstance(p, Polygon) and p.area > 4.0),
                                      key=lambda p: (p.bounds[0], p.bounds[1])), 1):
            rid = f"c_{f}_{i:03d}"
            spaces[rid] = Polygon(pm.exterior, pm.interiors).simplify(0.02)
            info[rid] = {"number": f"{f}-C{i:03d}", "type": "corridor", "sf": None, "ficm": [], "color": None}
        faces_m = faces_m.union(opened)
        outside = box(*shell.buffer(50).bounds).difference(shell)
        doors = place_doors([_tf(g, t) for g in fd["doors"]], spaces, outside)
        if f not in grade:
            doors = [d for d in doors if "outside" not in d["connects"]]
        # An exterior door whose outside lies over a lower floor opens onto a roof or terrace,
        # not grade: it is not an exit and the terrace is not on the map, so drop it.
        below = unary_union([per[b]["shell"] for b in FLOORS[:FLOORS.index(f)] if b not in below_ground])
        doors = [d for d in doors if "outside" not in d["connects"] or not below.contains(_outside_probe(d, shell))]
        doors += open_connections(spaces, doors)
        spaces, info, doors = merge_open_circulation(spaces, info, doors)
        per[f] = {"spaces": spaces, "info": info, "doors": doors, "shell": shell,
                  "all_faces": faces_m}
        stats["floors"][f] = {"m_per_pt": round(fd["m_per_pt"], 5), "scale_iqr": [round(x, 5) for x in fd["scale_iqr"]],
                              "scale_rooms": fd["n_scale"], "shift_m": [round(t[1], 2), round(t[2], 2)],
                              "labeled_rooms": len(fd["rooms"]), "door_symbols": len(fd["doors"]),
                              "doors": len(doors), "exits": sum("outside" in d["connects"] for d in doors)}

    if cfg.stair_suffixes:
        fill_stair_cores(per, FLOORS, cfg.stair_suffixes)
    # Stairs: stair rooms that overlap on consecutive floors.
    stairs, links, pairs = [], [], []
    for lo, hi in zip(FLOORS, FLOORS[1:]):
        for suf, rlo, rhi in _stair_pairs(per, lo, hi, cfg):
            a, b = per[lo]["spaces"][rlo], per[hi]["spaces"][rhi]
            pairs.append((lo, rlo, hi, rhi))
            poly = a.intersection(b)
            poly = max(getattr(poly, "geoms", [poly]), key=lambda p: p.area)
            bot, top = a.buffer(-0.6).representative_point(), b.buffer(-0.6).representative_point()
            rect = poly.minimum_rotated_rectangle
            sides = sorted(Point(p).distance(Point(q)) for p, q in zip(rect.exterior.coords, rect.exterior.coords[1:]))
            sid = f"stair_{lo}_{hi}_{suf}"
            stairs.append({"id": sid, "from_level": lid[lo], "to_level": lid[hi], "polygon": ring(poly.simplify(0.05)),
                           "bottom": [bot.x, bot.y], "top": [top.x, top.y], "width_m": float(np.clip(sides[0] / 2, 1.0, 2.5)),
                           "direction": "both", "is_enclosed": True})
            links.append({"id": f"link_{sid}", "type": "stairs", "from": {"level": lid[lo], "position": [bot.x, bot.y]},
                          "to": {"level": lid[hi], "position": [top.x, top.y]}, "cost_multiplier": 2.0, "bidirectional": True})

    reach = _reach(per, pairs, below_ground)
    # Every office/classroom has a door; when its symbol was not matched, infer one on the
    # longest wall shared with a reachable space (circulation first), and say so.
    while True:
        added = 0
        for f in FLOORS:
            for rid in [r for r in per[f]["spaces"] if (f, r) not in reach]:
                d = infer_door(rid, per[f], {r for r in per[f]["spaces"] if (f, r) in reach})
                if d:
                    per[f]["doors"].append(d)
                    added += 1
        if not added:
            break
        reach = _reach(per, pairs, below_ground)
    build.debug = (per, reach, stairs)  # for ad-hoc inspection

    scene_rooms, scene_doors, exits, walls, walkable, unreachable, inferred = [], [], [], [], {}, [], []
    for f in FLOORS:
        L = lid[f]
        keep = {r for r in per[f]["spaces"] if (f, r) in reach}
        unreachable += [per[f]["info"][r]["number"] for r in per[f]["spaces"] if r not in keep]
        for rid in sorted(keep):
            p = per[f]["spaces"][rid]
            inf = per[f]["info"][rid]
            scene_rooms.append({"id": rid, "level": L, "name": inf["number"], "type": inf["type"], "polygon": ring(p),
                                "area_m2": p.area, "centroid": [p.centroid.x, p.centroid.y], "has_window": False})
        floor_doors = [d for d in per[f]["doors"] if all(c == "outside" or c in keep for c in d["connects"])]
        gaps = []
        for i, d in enumerate(floor_doors, 1):
            did = f"door_{f}_{i:03d}"
            is_exit = "outside" in d["connects"]
            scene_doors.append({"id": did, "level": L, "segment": d["segment"], "width_m": d["width"],
                                "connects": d["connects"], "is_exit": is_exit,
                                "kind": d.get("kind") or ("double" if d["width"] >= 1.5 else "swing"), "fire_rated": None,
                                "opens_toward": "outside" if is_exit else None})
            box_ = door_gap_box(d["segment"], d["thick"] + GAP_OVERLAP_M)  # reach well into both rooms
            gaps.append(box_.union(d["bridge"]) if d.get("bridge") is not None else box_)
            if d.get("inferred"):
                inferred.append({"door": did, "reason": d["inferred"]})
            if is_exit:
                m = np.mean(d["segment"], axis=0)
                exits.append({"id": f"exit_{f}_{len([e for e in exits if e['level'] == L]) + 1:02d}", "level": L,
                              "door_id": did, "position": m.tolist(), "width_m": d["width"], "leads_to": "outside",
                              "label": f"Floor {f} exterior door", "assembly_point": None})
        material = per[f]["shell"].difference(per[f]["all_faces"]).difference(unary_union(gaps) if gaps else Polygon())
        # Rounding to the contract's 1 mm grid can pinch thin wall slivers, so snap first and
        # split again whatever comes back invalid or holed.
        snapped = shapely.make_valid(shapely.set_precision(material.buffer(0).simplify(0.01), 0.001))
        for w in _hole_free(unary_union([p for p in getattr(snapped, "geoms", [snapped]) if isinstance(p, Polygon)])):
            w = shapely.set_precision(w, 0.001)
            if w.area > 0.02 and w.is_valid and isinstance(w, Polygon) and not w.interiors:
                walls.append({"id": f"wall_{f}_{len(walls) + 1:04d}", "level": L, "polygon": ring(w),
                              "height_m": cfg.ceiling_m})
        free = [per[f]["spaces"][r] for r in keep] + gaps
        walk = walkable_from_parts(free, [], R)
        # ponytail: a 1 cm opening plus a 1 cm simplify drops hair-thin spikes and near-collinear
        # runs whose sliver triangles the validator reports as islands; a general fix belongs in walkable_from_parts if other scenes hit it.
        walk = walk.buffer(-0.005, join_style="mitre").buffer(0.005, join_style="mitre").simplify(0.01)
        walkable[L] = walkable_from_parts([walk], [], 1e-9)

    walkable = _reachable_walkable(walkable, exits, links)
    # A room the walkable never reaches (door box missed, room eroded to nothing) would strand
    # anyone placed in it: drop it, its doors and their exits, and say so.
    stranded = {r["id"] for r in scene_rooms
                if not walkable[r["level"]].intersects(Polygon(r["polygon"]).buffer(-0.05))}
    unreachable += sorted(stranded)
    scene_rooms = [r for r in scene_rooms if r["id"] not in stranded]
    scene_doors = [d for d in scene_doors if not stranded & set(d["connects"])]
    kept_doors = {d["id"] for d in scene_doors}
    exits = [e for e in exits if e["door_id"] in kept_doors]
    scene = {
        "schema_version": "1.0",
        "meta": {"name": cfg.name, "source": "blueprint", "source_file": cfg.source_note,
                 "units": "meters", "up_axis": "y", "scale_method": "manual", "scale_confidence": 0.95,
                 "generated_by": f"{generated_by} {_git_sha()}",
                 "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "levels": [{"id": lid[f], "name": f"Floor {f}", "elevation_m": elev[f], "ceiling_height_m": cfg.ceiling_m} for f in FLOORS],
        "walls": walls, "rooms": scene_rooms, "doors": scene_doors, "exits": exits, "stairs": stairs,
        "elevators": [{"id": f"elev_{suf}", "levels": [lid[f] for f in FLOORS if f"r_{f}{suf}" in per[f]["spaces"]],
                       "position": list(per[FLOORS[0]]["spaces"][f"r_{FLOORS[0]}{suf}"].centroid.coords[0]),
                       "usable_in_fire": False}
                      for suf in cfg.elevator_suffixes if f"r_{FLOORS[0]}{suf}" in per[FLOORS[0]]["spaces"]],
        "obstacles": [], "points_of_interest": [],
        "walkable": {L: walkable_to_contract(w) for L, w in walkable.items()},
        "navmesh": navmesh_from_walkable(walkable, {lid[f]: elev[f] for f in FLOORS}, R),
        "links": links,
        "assets": {"display_model": "display.glb", "floorplan_overlay": "plan.svg", "navmesh_debug": "display.glb"},
        "warnings": [],
        "extensions": {ext: {
            "rooms": {rid: {"ficm_candidates": inf["ficm"], "sf": inf["sf"], "hatch_rgb": inf["color"],
                            **({"merged_open_members": inf["members"]} if inf.get("members") else {})}
                      for f in FLOORS for rid, inf in per[f]["info"].items()},
            "scale_method": "room_area_labels", "unreachable": unreachable,
            "inferred_stair_cores": [inf["number"] for f in FLOORS for inf in per[f]["info"].values() if inf.get("inferred")], "inferred_doors": inferred, "floor_to_floor_m": cfg.floor_height_m,
            "scale": {f: stats["floors"][f]["m_per_pt"] for f in FLOORS},
            "alignment_shift_m": {f: stats["floors"][f]["shift_m"] for f in FLOORS},
            "outline": {lid[f]: ring(max(getattr(per[f]["shell"], "geoms", [per[f]["shell"]]), key=lambda p: p.area).simplify(0.05))
                        for f in FLOORS}}},
    }
    stats["unreachable"] = len(unreachable)
    kinds = collections.Counter("inferred" if d["id"] in {i["door"] for i in inferred} else d["kind"] for d in scene_doors)
    stats.update(rooms=len(scene_rooms), doors=dict(kinds), exits=len(exits), unreachable_ids=unreachable)
    return _round(scene), stats


def _reachable_walkable(walkable: dict, exits: list, links: list) -> dict:
    """Keep walkable parts connected to an exit through stairs/links (others are navmesh islands)."""
    parts = [(L, i, p) for L, mp in walkable.items() for i, p in enumerate(mp.geoms)]
    g = nx.Graph()
    g.add_nodes_from((L, i) for L, i, _ in parts)
    g.add_node("exit")

    def near(level: str, xz) -> list:
        return [(L, i) for L, i, p in parts if L == level and p.distance(Point(xz)) <= 1.0]

    for e in exits:
        g.add_edges_from(("exit", n) for n in near(e["level"], e["position"]))
    for lk in links:
        for a in near(lk["from"]["level"], lk["from"]["position"]):
            g.add_edges_from((a, b) for b in near(lk["to"]["level"], lk["to"]["position"]))
    keep = nx.node_connected_component(g, "exit")
    return {L: MultiPolygon([p for i, p in enumerate(mp.geoms) if (L, i) in keep]) for L, mp in walkable.items()}


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def write_glb(scene: dict, path: Path) -> None:
    """One named mesh per level for walls (walls_L1..), a 5 cm floor slab (floor_L1..) under the
    walkable area, and stair footprints as 0.3 m blocks (stairs)."""
    elev = {lv["id"]: lv["elevation_m"] for lv in scene["levels"]}
    out = trimesh.Scene()
    for L in elev:
        walls = [extrude_xz(Polygon(w["polygon"]), elev[L], w["height_m"]) for w in scene["walls"] if w["level"] == L]
        if walls:
            out.add_geometry(trimesh.util.concatenate(walls), node_name=f"walls_{L}", geom_name=f"walls_{L}")
        slab = [extrude_xz(Polygon(wp["exterior"], wp["holes"]), elev[L] - 0.05, 0.05)
                for wp in scene["walkable"].get(L, {}).get("polygons", [])]
        if slab:
            out.add_geometry(trimesh.util.concatenate(slab), node_name=f"floor_{L}", geom_name=f"floor_{L}")
    stairs = [extrude_xz(Polygon(s["polygon"]), elev[s["from_level"]], 0.3) for s in scene["stairs"]]
    if stairs:
        out.add_geometry(trimesh.util.concatenate(stairs), node_name="stairs", geom_name="stairs")
    out.export(path)


def order_floors(ids: list[str], how: str) -> list[str]:
    """Bottom-to-top order of floor ids."""
    if how == "cmu":
        letters = sorted((f for f in ids if not f.isdigit()), reverse=True)  # C below B below A
        return letters + sorted((f for f in ids if f.isdigit()), key=int)
    return sorted((f for f in ids if not f.isdigit())) + sorted((f for f in ids if f.isdigit()), key=int)
