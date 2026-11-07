"""Multi-level contract scene for the Tepper Building from CMU ESIM CAD sheets (floors 1-5).

Inputs (CMU-internal, never committed): data/cmu/raw/tepper/TSB-<f>-ESIM-Base.pdf (CAD layers as
PDF optional content: A-AREA room boundaries, ARCH|A-DOOR door symbols, A-SPAC-EXTR gross outline,
room number and "<n>sf" labels) and TSB-<f>-ESIM-Type.pdf (rooms hatched by FICM space type, color
key in TSB-1/2-ESIM-TypeChart.pdf).

Method, per floor: rooms = polygonized A-AREA linework holding a room-number label; scale =
median of sqrt(labeled sf * 0.092903 / traced area); type = majority hatch color under the room;
doors = A-DOOR symbols placed on the wall strip between the two spaces they touch (outside when
the strip faces the exterior); walls = gross outline minus rooms minus door openings. Floors
stack at FLOOR_TO_FLOOR_M; stairwells (W07) that overlap on consecutive floors become stairs +
links. Rooms with no door/stair path to an exit are left out of `rooms` (listed in
extensions.tepper.unreachable) so the scene stays valid.

WS-A's generic pipeline finds no rooms on these sheets (blueprint/FAILURES_CMU.md #3), so this
is a Tepper-specific reader that writes the standard contract.

    python -m tools.tepper_to_scene data/cmu/raw/tepper -o samples/cmu/tepper
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
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

from contract.validate_scene import validate
from ingest_lidar.geometry import (door_gap_box, exit_reachable_parts, extrude_xz, navmesh_from_walkable, ring,
                                   walkable_from_parts, walkable_to_contract)
from tools.layouts import _round

FLOORS = ["1", "2", "3", "4", "5"]
FLOOR_TO_FLOOR_M = 4.3  # OSM has no height/levels for Tepper; typical for this building class
CEILING_M = 3.5
SQFT_M2 = 0.092903
R = 0.25
EXIT_FLOORS = {"1", "2"}  # 1 = Forbes Ave grade, 2 = main entrance from the quad (cmu.edu/tepper directions)
ROOM_NO = re.compile(r"^(\d{4}[A-Z]?|[A-Z]{3}\d-\d+[A-Z]?)$")
SF = re.compile(r"^([\d,]+)\s*sf$", re.I)

# FICM code -> contract room type. Codes not listed map to "unknown".
FICM_TYPE = {"110": "classroom", "115": "storage", "210": "classroom", "215": "storage", "220": "classroom",
             "225": "storage", "250": "office", "310": "office", "310.1": "office", "315": "storage",
             "315.1": "storage", "350": "office", "455": "storage", "520": "unknown", "525": "storage",
             "610": "lobby", "615": "storage", "615.1": "storage", "630": "lobby", "635": "storage",
             "650": "lobby", "655": "storage", "680": "office", "685": "storage", "710": "mechanical",
             "730": "storage", "880": "lobby", "W02": "unknown", "W06": "corridor", "W07": "stairwell",
             "X02": "storage", "X03": "restroom", "X03.3": "restroom", "Y04": "mechanical", "070.1": "unknown"}


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
    """Hatch color -> FICM codes, from the swatch beside each code line on the type charts."""
    out: dict[tuple, set] = collections.defaultdict(set)
    for pdf in chart_pdfs:
        page = fitz.open(pdf)[0]
        codes = [w for w in page.get_text("words") if re.fullmatch(r"[0-9WXY]\d{2}(\.\d)?", w[4])]
        for d in page.get_drawings():
            r = d["rect"]
            if not d.get("fill") or r.width > 60 or r.width < 3:
                continue
            cy = (r.y0 + r.y1) / 2
            # The code sits just right of its swatch on the same row (the chart has two columns).
            near = [w[4] for w in codes if w[1] - 4 <= cy <= w[3] + 4 and r.x1 <= w[0] <= r.x1 + 60]
            if near:
                out[_rgb(d["fill"])].add(near[0])
    return {k: sorted(v) for k, v in out.items()}


def _labels(page: fitz.Page, leaders: list[LineString]) -> list[dict]:
    """Room number + sf pairs with the point that identifies their room (leader end when present)."""
    words = page.get_text("words")
    sfs = [w for w in words if SF.match(w[4])]
    out = []
    for w in words:
        if not ROOM_NO.match(w[4]):
            continue
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        below = [s for s in sfs if 0 < s[1] - w[1] < 10 and abs((s[0] + s[2]) / 2 - cx) < 12]
        sf = float(SF.match(below[0][4]).group(1).replace(",", "")) if below else None
        out.append({"number": w[4], "at": Point(cx, cy), "bbox": box(w[0], w[1], w[2], w[3]), "sf": sf})
    return out


def read_floor(base_pdf: Path, type_pdf: Path, colors: dict) -> dict:
    page = fitz.open(base_pdf)[0]
    drawings = page.get_drawings()
    by_layer: dict[str, list] = collections.defaultdict(list)
    for d in drawings:
        by_layer[d.get("layer") or ""].append(d)
    area_lines = [ln for d in by_layer["A-AREA"] for ln in _lines(d)]
    faces = [f for f in polygonize(unary_union(area_lines)) if f.area > 1.0]
    leaders = [ln for d in by_layer["A-SPAC-IDEN-LEDR"] for ln in _lines(d)]
    gross = [p for d in by_layer["A-SPAC-EXTR"] for p in polygonize(unary_union(_lines(d)))]
    shell = max(gross, key=lambda p: p.area) if gross else unary_union(faces).convex_hull

    rooms: dict[str, dict] = {}
    for lab in _labels(page, leaders):
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

    # Type: majority hatch color inside each room on the Type sheet.
    fills = [(box(*d["rect"]), _rgb(d["fill"])) for d in fitz.open(type_pdf)[0].get_drawings()
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
    used = [r["poly"] for r in rooms.values()]
    others = [f for f in faces if not any(f.equals(u) for u in used)]
    return {"rooms": rooms, "unlabeled": others, "shell": shell, "doors": symbols, "m_per_pt": m_per_pt,
            "scale_iqr": [float(np.percentile(ratios, 25)), float(np.percentile(ratios, 75))], "n_scale": len(ratios),
            "treads": stairs_layer}


# Hatch color -> contract type, decided from the FICM codes each color carries on the charts
# (legend() output); where one color covers several codes we take the evacuation-relevant or
# dominant one. Gray is W07 stairway only when stair treads are drawn in the room.
COLOR_TYPE = {(1.0, 0.75, 0.5): "classroom", (1.0, 1.0, 0.5): "storage", (0.5, 0.62, 1.0): "office",
              (0.84, 0.84, 0.84): "storage", (1.0, 0.0, 0.0): "unknown", (0.62, 1.0, 0.5): "storage",
              (1.0, 0.5, 0.62): "lobby", (1.0, 1.0, 0.0): "storage", (0.5, 1.0, 1.0): "office",
              (0.25, 1.0, 0.0): "corridor", (0.52, 0.52, 0.52): "storage", (1.0, 0.0, 0.75): "restroom",
              (1.0, 0.5, 0.5): "mechanical"}
OPEN_TO_BELOW = (0.0, 0.25, 1.0)  # FICM 070.1: atrium void, not floor
STAIR_SUFFIX = ("901", "902", "903")
ELEVATOR_SUFFIX = ("912", "913", "914")
CORE_SUFFIX = STAIR_SUFFIX + ("911",) + ELEVATOR_SUFFIX
WALL_REACH_M = 0.6  # widest wall a door symbol may cross
DOOR_W = (0.8, 2.0)
EXTERIOR_WALL_M = 0.3  # A-SPAC-EXTR is the outer face of the exterior wall
CORRIDOR_MIN_M = 0.9
INFERRED_DOOR_M = 0.9
INFER_REACH_M = 1.2  # inferred doors may cross thicker walls (glazed suite fronts)
OPEN_GAP_M = 0.08  # spaces closer than this have no wall between them


def room_type(number: str, r: dict, s: float) -> str:
    suffix = number[1:4]
    if suffix in STAIR_SUFFIX:
        return "stairwell"
    if suffix in ELEVATOR_SUFFIX:
        return "unknown"
    t = COLOR_TYPE.get(r["color"], "unknown")
    if t == "mechanical" and r["poly"].area * s * s > 60:
        return "lobby"  # 650 lounge / 635 food service share the Y04 color; big ones are not shafts
    return t


def _align(floors: dict) -> dict[str, tuple[float, float, float]]:
    """Per floor (scale m/pt, dx, dz): the sheets are each fit to the page, so scale and origin
    differ; translation is the median shift of the vertical core rooms (stairs x901-903,
    elevators x911-914) onto floor 1."""
    out = {}
    base = floors[FLOORS[0]]
    for f, fd in floors.items():
        s = fd["m_per_pt"]
        shifts = []
        for suf in CORE_SUFFIX:
            a, b = base["rooms"].get(FLOORS[0] + suf), fd["rooms"].get(f + suf)
            if a and b:
                ca, cb = a["poly"].centroid, b["poly"].centroid
                shifts.append((ca.x * base["m_per_pt"] - cb.x * s, ca.y * base["m_per_pt"] - cb.y * s))
        dx, dz = np.median(np.array(shifts), axis=0)
        out[f] = (s, float(dx), float(dz))
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
                        "shared_m": length})
            have.add(frozenset((a, b)))
    return out


def _reach(per: dict, stairs: list) -> set:
    """(floor, space) nodes with a door/stair path to outside."""
    g = nx.Graph()
    g.add_node("outside")
    for f, pf in per.items():
        g.add_nodes_from((f, r) for r in pf["spaces"])
        for d in pf["doors"]:
            a, b = d["connects"]
            g.add_edge((f, a) if a != "outside" else "outside", (f, b) if b != "outside" else "outside")
    for st in stairs:
        lo, hi = st["from_level"][1:], st["to_level"][1:]
        suf = st["id"].rsplit("_", 1)[1]
        g.add_edge((lo, f"r_{lo}{suf}"), (hi, f"r_{hi}{suf}"))
    return nx.node_connected_component(g, "outside")


def infer_door(rid: str, pf: dict, reachable: set[str]) -> dict | None:
    """A 0.9 m door on the longest wall `rid` shares (>= 0.8 m, wall <= INFER_REACH_M) with a
    reachable space; corridors and lobbies win over other rooms. None when nothing qualifies."""
    a = pf["spaces"][rid]
    best = None
    for other in reachable:
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


def build(raw: Path) -> tuple[dict, dict]:
    colors = legend(sorted(raw.glob("TSB-*-ESIM-TypeChart.pdf")))
    floors = {f: read_floor(raw / f"TSB-{f}-ESIM-Base.pdf", raw / f"TSB-{f}-ESIM-Type.pdf", colors) for f in FLOORS}
    align = _align(floors)
    lid = {f: f"L{f}" for f in FLOORS}
    elev = {f: (int(f) - 1) * FLOOR_TO_FLOOR_M for f in FLOORS}
    stats: dict = {"floors": {}}
    per: dict[str, dict] = {}
    for f, fd in floors.items():
        t = align[f]
        spaces, info = {}, {}
        for num, r in fd["rooms"].items():
            if r["color"] == OPEN_TO_BELOW or num.startswith("VPN"):
                continue  # atrium voids and shafts are not floor
            rid = f"r_{num}"
            spaces[rid] = _tf(r["poly"], t)
            info[rid] = {"number": num, "type": room_type(num, r, 1.0), "sf": r["sf"],
                         "ficm": r["ficm"], "color": r["color"]}
            if info[rid]["type"] == "mechanical" and spaces[rid].area > 60:
                info[rid]["type"] = "lobby"
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
        if f not in EXIT_FLOORS:
            doors = [d for d in doors if "outside" not in d["connects"]]
        doors += open_connections(spaces, doors)
        spaces, info, doors = merge_open_circulation(spaces, info, doors)
        per[f] = {"spaces": spaces, "info": info, "doors": doors, "shell": shell,
                  "all_faces": faces_m}
        stats["floors"][f] = {"m_per_pt": round(fd["m_per_pt"], 5), "scale_iqr": [round(x, 5) for x in fd["scale_iqr"]],
                              "scale_rooms": fd["n_scale"], "shift_m": [round(t[1], 2), round(t[2], 2)],
                              "labeled_rooms": len(fd["rooms"]), "door_symbols": len(fd["doors"]),
                              "doors": len(doors), "exits": sum("outside" in d["connects"] for d in doors)}

    # Stairs: the same stair core (x901-x903) on consecutive floors.
    stairs, links = [], []
    for lo, hi in zip(FLOORS, FLOORS[1:]):
        for suf in STAIR_SUFFIX:
            a, b = per[lo]["spaces"].get(f"r_{lo}{suf}"), per[hi]["spaces"].get(f"r_{hi}{suf}")
            if a is None or b is None or a.intersection(b).area < 0.3 * min(a.area, b.area):
                continue
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

    reach = _reach(per, stairs)
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
        reach = _reach(per, stairs)
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
            gaps.append(door_gap_box(d["segment"], d["thick"] + 0.1))
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
                              "height_m": CEILING_M})
        free = [per[f]["spaces"][r] for r in keep] + gaps
        walk = walkable_from_parts(free, [], R)
        # ponytail: a 1 cm opening plus a 1 cm simplify drops hair-thin spikes and near-collinear
        # runs whose sliver triangles the validator reports as islands; a general fix belongs in walkable_from_parts if other scenes hit it.
        walk = walk.buffer(-0.005, join_style="mitre").buffer(0.005, join_style="mitre").simplify(0.01)
        walkable[L] = walkable_from_parts([walk], [], 1e-9)

    walkable = _reachable_walkable(walkable, exits, links)
    scene = {
        "schema_version": "1.0",
        "meta": {"name": "tepper_building", "source": "blueprint",
                 "source_file": "CMU ESIM TSB-{1..5}-ESIM-Base.pdf + -Type.pdf (CMU-internal, not redistributed)",
                 "units": "meters", "up_axis": "y", "scale_method": "manual", "scale_confidence": 0.95,
                 "generated_by": f"tools.tepper_to_scene {_git_sha()}",
                 "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "levels": [{"id": lid[f], "name": f"Floor {f}", "elevation_m": elev[f], "ceiling_height_m": CEILING_M} for f in FLOORS],
        "walls": walls, "rooms": scene_rooms, "doors": scene_doors, "exits": exits, "stairs": stairs,
        "elevators": [{"id": f"elev_{suf}", "levels": [lid[f] for f in FLOORS if f"r_{f}{suf}" in per[f]["spaces"]],
                       "position": list(per[FLOORS[0]]["spaces"][f"r_1{suf}"].centroid.coords[0]), "usable_in_fire": False}
                      for suf in ELEVATOR_SUFFIX if f"r_1{suf}" in per[FLOORS[0]]["spaces"]],
        "obstacles": [], "points_of_interest": [],
        "walkable": {L: walkable_to_contract(w) for L, w in walkable.items()},
        "navmesh": navmesh_from_walkable(walkable, {lid[f]: elev[f] for f in FLOORS}, R),
        "links": links,
        "assets": {"display_model": "display.glb", "floorplan_overlay": "plan.svg", "navmesh_debug": "display.glb"},
        "warnings": [],
        "extensions": {"tepper": {
            "rooms": {rid: {"ficm_candidates": inf["ficm"], "sf": inf["sf"], "hatch_rgb": inf["color"],
                            **({"merged_open_members": inf["members"]} if inf.get("members") else {})}
                      for f in FLOORS for rid, inf in per[f]["info"].items()},
            "scale_method": "room_area_labels", "unreachable": unreachable, "inferred_doors": inferred, "floor_to_floor_m": FLOOR_TO_FLOOR_M,
            "scale": {f: stats["floors"][f]["m_per_pt"] for f in FLOORS},
            "alignment_shift_m": {f: stats["floors"][f]["shift_m"] for f in FLOORS}}},
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw", type=Path, help="folder with TSB-<f>-ESIM-{Base,Type}.pdf and TypeChart PDFs")
    ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args(argv)
    scene, stats = build(a.raw)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "scene.json").write_text(json.dumps(scene, indent=1) + "\n")
    write_glb(scene, a.out / "display.glb")
    issues = validate(scene)
    errors = [i for i in issues if i["severity"] == "error"]
    print(json.dumps(stats, indent=1))
    print(f"validator: {len(errors)} errors, {len(issues) - len(errors)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
