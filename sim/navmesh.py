"""Multi-level navmesh graph, point location, A*, funnel and exit distance fields.

All distances in meters. Points are [x, z] plus a level index. Triangles touching along a shared
edge are neighbours; vertices are merged at 1 mm so separately indexed copies
still connect.
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union
from typing import NamedTuple

INF = math.inf
MAX_EDGE_M = 2.0  # refine coarse triangles near doors so blocking a door stays local
DOOR_ZONE_M = 0.05
SEARCH_LIMIT_M = 600.0  # point-to-point searches stop beyond this cost
REFINE_PASSES = 5  # each pass halves long door-side edges; slivers would otherwise cascade
DEFAULT_STAIR_WIDTH_M = 1.2


class Link(NamedTuple):
    """A directed level-to-level connection (stairs, ramp) between two triangles."""

    id: str
    kind: str
    a_pt: np.ndarray
    b_pt: np.ndarray
    a_lv: int
    b_lv: int
    length_m: float  # walked length along the stair
    cost_mult: float
    down: bool
    width_m: float


class Waypoint(NamedTuple):
    """Next point to reach on level `lv`; `link` set means: get there by taking that link."""

    pt: np.ndarray
    lv: int
    link: Link | None = None


class NavMesh:
    """Triangle graph over all levels plus level links. `mult` holds per-triangle entry costs.

    Levels are referred to by index into `level_ids`. Triangles of one level
    connect through shared edges; levels connect only through links.
    """

    def __init__(self, scene: dict) -> None:
        nav = scene["navmesh"]
        self.level_ids = [lv["id"] for lv in scene["levels"]]
        self.elev = [float(lv["elevation_m"]) for lv in scene["levels"]]
        lv_index = {lid: i for i, lid in enumerate(self.level_ids)}
        raw3 = np.asarray(nav["vertices"], dtype=float)
        _, inverse = np.unique(np.round(raw3, 3), axis=0, return_inverse=True)
        uniq = np.zeros((inverse.max() + 1, 2))
        uniq[inverse.ravel()] = raw3[:, [0, 2]]
        tris = inverse.ravel()[np.asarray(nav["triangles"], dtype=int)]
        tri_lv = np.array([lv_index.get(lv, 0) for lv in nav["triangle_level"]], dtype=int)
        zones = [_door_zone(scene, lid) for lid in self.level_ids]
        self.verts, self.tris, self.tri_level = refine(uniq, tris, tri_lv, MAX_EDGE_M, zones)
        self.centroids = self.verts[self.tris].mean(axis=1)
        self.polys = [Polygon(self.verts[t]) for t in self.tris]
        self.lv_tris = [np.flatnonzero(self.tri_level == i) for i in range(len(self.level_ids))]
        self.lv_tree = [shapely.STRtree([self.polys[t] for t in idx]) for idx in self.lv_tris]
        self.walk, self.walk_los, self.walk_move = [], [], []
        for lid in self.level_ids:
            polys = [Polygon(p["exterior"], p["holes"]) for p in scene["walkable"].get(lid, {"polygons": []})["polygons"]]
            w = unary_union(polys) if polys else Polygon()
            # funnel paths run along edges; the 2 cm move tolerance absorbs float error
            for lst, g in ((self.walk, w), (self.walk_los, w.buffer(0.05)), (self.walk_move, w.buffer(0.02))):
                shapely.prepare(g)
                lst.append(g)
        self._build_adjacency()
        self.links: dict[tuple[int, int], Link] = {}
        for link in _scene_links(scene, self):
            self._add_link(link)
        self._build_edges()
        self.mult = np.ones(len(self.tris))

    def _build_adjacency(self) -> None:
        owner: dict[tuple[int, int], int] = {}
        self.portals: dict[tuple[int, int], tuple[int, int]] = {}
        self.neighbors: list[list[int]] = [[] for _ in range(len(self.tris))]
        for ti, tri in enumerate(self.tris):
            for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                k = (min(a, b), max(a, b))
                other = owner.get(k)
                if other is None:
                    owner[k] = ti
                    continue
                self.neighbors[ti].append(other)
                self.neighbors[other].append(ti)
                self.portals[(ti, other)] = self.portals[(other, ti)] = (int(a), int(b))

    def _add_link(self, link: Link) -> None:
        ta = int(self.locate(link.a_pt[None], link.a_lv)[0])
        tb = int(self.locate(link.b_pt[None], link.b_lv)[0])
        if ta == tb or (ta, tb) in self.portals:
            return
        self.links[(ta, tb)] = link
        self.neighbors[ta].append(tb)

    def _build_edges(self) -> None:
        pairs = list(self.portals) + list(self.links)
        arr = np.array(pairs, dtype=int).reshape(-1, 2)
        self.edge_src, self.edge_dst = arr[:, 0], arr[:, 1]
        lens = np.linalg.norm(self.centroids[self.edge_src] - self.centroids[self.edge_dst], axis=1)
        for k, (i, j) in enumerate(pairs[len(self.portals):], start=len(self.portals)):
            ln = self.links[(i, j)]
            lens[k] = ln.length_m * ln.cost_mult
        self.edge_len = np.maximum(lens, 1e-4)
        self.edge_stair = np.array([""] * len(self.portals) + [self.links[k].id for k in pairs[len(self.portals):]])
        link_pairs = pairs[len(self.portals):]
        self.edge_up = np.zeros(len(pairs), dtype=bool)
        self.edge_to_lv = self.tri_level[self.edge_dst]
        for k, key in enumerate(link_pairs, start=len(self.portals)):
            ln = self.links[key]
            self.edge_up[k] = self.elev[ln.b_lv] > self.elev[ln.a_lv]
        self.exit_levels: set[int] = set()  # set by World; up-stairs may only lead to these levels
        self.stair_mult: dict[str, float] = {}  # hazard multiplier per stair id (inf = blocked)

    def walk_len(self, i: int, j: int) -> float:
        """Metres walked going from triangle i to neighbour j (centroids, or the link length)."""
        ln = self.links.get((i, j))
        return ln.length_m if ln else float(np.linalg.norm(self.centroids[i] - self.centroids[j]))

    # ---- point queries -------------------------------------------------
    def locate(self, pts: np.ndarray, lv: int | np.ndarray = 0) -> np.ndarray:
        """Global triangle index for each [x, z] point on its level; nearest triangle when off-mesh."""
        pts = np.atleast_2d(pts)
        lvs = np.broadcast_to(np.asarray(lv, dtype=int), (len(pts),))
        out = np.full(len(pts), -1, dtype=int)
        for li in np.unique(lvs):
            rows = np.flatnonzero(lvs == li)
            geoms = shapely.points(pts[rows])
            tree, idx = self.lv_tree[li], self.lv_tris[li]
            hit_pt, hit_tri = tree.query(geoms, predicate="intersects")
            local = np.full(len(rows), -1, dtype=int)
            local[hit_pt[::-1]] = hit_tri[::-1]  # first hit wins
            miss = np.flatnonzero(local < 0)
            if len(miss):
                near = tree.query_nearest(geoms[miss], return_distance=False, all_matches=False)
                local[miss[near[0]]] = near[1]
            out[rows] = idx[local]
        return out

    def is_walkable(self, pts: np.ndarray, lv: int | np.ndarray = 0) -> np.ndarray:
        """Bool per [x, z] point: inside its level's walkable area (2 cm tolerance)."""
        pts = np.atleast_2d(pts)
        lvs = np.broadcast_to(np.asarray(lv, dtype=int), (len(pts),))
        out = np.zeros(len(pts), dtype=bool)
        for li in np.unique(lvs):
            rows = lvs == li
            out[rows] = shapely.intersects_xy(self.walk_move[li], pts[rows, 0], pts[rows, 1])
        return out

    def snap(self, pt: list[float] | np.ndarray, lv: int = 0, inset_m: float = 0.02) -> np.ndarray:
        """Nearest walkable point on level lv to `pt`, pulled `inset_m` inward when it was outside."""
        p = Point(pt)
        walk = self.walk[lv]
        if walk.is_empty or walk.contains(p):
            return np.asarray(pt, dtype=float)
        q = np.array(nearest_points(walk, p)[0].coords[0])
        c = self.centroids[self.locate(q[None], lv)[0]]
        d = c - q
        n = np.linalg.norm(d)
        return q + d / n * min(inset_m, n) if n > 0 else q

    def line_of_sight(self, src: np.ndarray, dsts: np.ndarray, lv: int = 0) -> np.ndarray:
        """Bool per destination: segment src->dst stays on level lv's (slightly buffered) walkable."""
        dsts = np.atleast_2d(dsts)
        if len(dsts) == 0:
            return np.zeros(0, dtype=bool)
        coords = np.stack([np.broadcast_to(src, dsts.shape), dsts], axis=1)
        same = np.all(np.abs(coords[:, 0] - coords[:, 1]) < 1e-6, axis=1)
        coords[same, 1] += 1e-4
        return shapely.covers(self.walk_los[lv], shapely.linestrings(coords))

    def clear_segments(self, a: np.ndarray, b: np.ndarray, lv: np.ndarray) -> np.ndarray:
        """Bool per row: straight move a[i] -> b[i] stays walkable on level lv[i]."""
        out = np.zeros(len(a), dtype=bool)
        if len(a) == 0:
            return out
        coords = np.stack([a, b], axis=1).copy()
        same = np.all(np.abs(coords[:, 0] - coords[:, 1]) < 1e-6, axis=1)
        coords[same, 1] += 1e-4
        lines = shapely.linestrings(coords)
        for li in np.unique(lv):
            rows = lv == li
            out[rows] = shapely.covers(self.walk_move[li], lines[rows])
        return out

    # ---- search --------------------------------------------------------
    def step_cost(self, i: int, j: int) -> float:
        """Cost of moving from triangle i into neighbour j (meters times j's multiplier)."""
        ln = self.links.get((i, j))
        if ln:
            return ln.length_m * ln.cost_mult * self.stair_mult.get(ln.id, 1.0) * self.mult[j]
        return float(np.linalg.norm(self.centroids[i] - self.centroids[j])) * self.mult[j]

    def _weights(self, use_links: bool | str) -> np.ndarray:
        """Directed edge costs: base length times the entered triangle's and the stair's multipliers.

        use_links: True/"all" every link (helpers may climb), False/"none" no links, "down" no
        upward link, "evac" downward links plus upward ones into a level that has an exit.
        """
        w = self.edge_len * self.mult[self.edge_dst]
        if use_links in (False, "none"):
            return np.where(self.edge_stair != "", INF, w)
        if use_links == "down":
            w = np.where(self.edge_up, INF, w)
        elif use_links == "evac":
            to_exit_level = np.isin(self.edge_to_lv, list(self.exit_levels))
            w = np.where(self.edge_up & ~to_exit_level, INF, w)
        for sid, m in self.stair_mult.items():
            w = np.where(self.edge_stair == sid, w * m, w)
        return w

    def _graph(self, use_links: bool | str, reverse: bool) -> csr_matrix:
        key = (use_links, reverse, self.mult.tobytes(), tuple(sorted(self.stair_mult.items())))
        cache = getattr(self, "_graph_cache", {})
        if key not in cache:
            w = self._weights(use_links)
            ok = np.isfinite(w)
            src, dst = (self.edge_dst, self.edge_src) if reverse else (self.edge_src, self.edge_dst)
            n = len(self.tris)
            cache = {k: v for k, v in cache.items() if k[2:] == key[2:]}  # drop stale cost versions
            cache[key] = csr_matrix((w[ok], (src[ok], dst[ok])), shape=(n, n))
            self._graph_cache = cache
        return cache[key]

    def costs_from(self, start: int, use_links: bool = True) -> np.ndarray:
        """Route cost (m, hazard weighted) from triangle `start` to every triangle (inf beyond the limit)."""
        return dijkstra(self._graph(use_links, False), indices=start, limit=SEARCH_LIMIT_M)

    def astar(self, start: int, goal: int, use_links: bool = True) -> list[int] | None:
        """Cheapest triangle corridor from start to goal, or None when unreachable.

        ponytail: one-to-all Dijkstra in scipy (C) instead of a Python A*; same corridor, 20x faster here.
        """
        if start == goal:
            return [start]
        dist, pred = dijkstra(self._graph(use_links, False), indices=start, return_predecessors=True,
                              limit=SEARCH_LIMIT_M)
        if not np.isfinite(dist[goal]):
            return None
        path = [goal]
        while path[-1] != start:
            path.append(int(pred[path[-1]]))
        return path[::-1]

    def distance_field(self, seed: int, use_links: bool | str = True) -> tuple[np.ndarray, np.ndarray]:
        """Cost-to-go from every triangle to `seed` and the next triangle toward it (-1 at seed).

        use_links=False keeps routes on one level (people who cannot use stairs).
        """
        # Reverse edges: distances from seed in the transpose are distances to seed.
        return dijkstra(self._graph(use_links, True), indices=seed, return_predecessors=True)

    def corridor(self, start: int, nxt: np.ndarray) -> list[int]:
        """Follow a field's next-triangle pointers from start to the seed."""
        path = [start]
        while nxt[path[-1]] >= 0 and len(path) <= len(self.tris):
            path.append(int(nxt[path[-1]]))
        return path

    def funnel(self, start: np.ndarray, goal: np.ndarray, corridor: list[int]) -> list[np.ndarray]:
        """String-pull a same-level triangle corridor into points (simple stupid funnel algorithm)."""
        portals = [(start, start)]
        verts, cents = self.verts, self.centroids
        for a, b in zip(corridor[:-1], corridor[1:]):
            pa, pb = self.portals[(a, b)]
            (ax, az), (bx, bz), (cx, cz) = verts[pa], verts[pb], cents[a]
            fx, fz = (ax + bx) / 2 - cx, (az + bz) / 2 - cz
            right_is_a = fx * (az - cz) - fz * (ax - cx) < 0
            portals.append((verts[pb], verts[pa]) if right_is_a else (verts[pa], verts[pb]))  # (left, right)
        portals.append((goal, goal))
        return _string_pull(portals)

    def waypoints(self, start: np.ndarray, goal: np.ndarray, corridor: list[int]) -> list[Waypoint]:
        """Waypoints after `start` along a corridor that may cross levels through links."""
        out: list[Waypoint] = []
        seg, src = [corridor[0]], np.asarray(start, float)
        for a, b in zip(corridor[:-1], corridor[1:]):
            link = self.links.get((a, b))
            if link is None:
                seg.append(b)
                continue
            lv = int(self.tri_level[a])
            out += [Waypoint(p, lv) for p in self.funnel(src, link.a_pt, seg)[1:]]
            out.append(Waypoint(link.b_pt, link.b_lv, link))
            seg, src = [b], link.b_pt
        lv = int(self.tri_level[corridor[-1]])
        out += [Waypoint(p, lv) for p in self.funnel(src, np.asarray(goal, float), seg)[1:]]
        return out

    def find_path(self, a: np.ndarray, b: np.ndarray, lv_a: int = 0, lv_b: int | None = None) -> list[Waypoint] | None:
        """Waypoints from a (level lv_a) to b (level lv_b), A* corridor + funnel; None when unreachable."""
        ta = int(self.locate(np.asarray(a)[None], lv_a)[0])
        tb = int(self.locate(np.asarray(b)[None], lv_a if lv_b is None else lv_b)[0])
        corr = self.astar(ta, tb)
        return None if corr is None else [Waypoint(np.asarray(a, float), lv_a)] + self.waypoints(a, b, corr)

    def tris_in(self, geom: Polygon | LineString, lv: int) -> np.ndarray:
        """Triangles of level lv whose area intersects `geom` (a room polygon or door segment)."""
        return self.lv_tris[lv][self.lv_tree[lv].query(geom, predicate="intersects")]

    def tris_with_centroid_in(self, poly: Polygon, lv: int) -> np.ndarray:
        """Triangles of level lv whose centroid lies inside `poly`."""
        idx = self.lv_tris[lv]
        return idx[shapely.contains_xy(poly, self.centroids[idx, 0], self.centroids[idx, 1])]


def _scene_links(scene: dict, nav: "NavMesh") -> list[Link]:
    """Directed links from scene.links (or from scene.stairs when there are no links)."""
    lv = {lid: i for i, lid in enumerate(nav.level_ids)}
    stairs = scene.get("stairs", [])
    raw = [(ln["id"], ln["type"], ln["from"], ln["to"], float(ln.get("cost_multiplier", 1.0)),
            bool(ln.get("bidirectional", True))) for ln in scene.get("links", [])]
    if not raw:
        raw = [(st["id"], "stairs", {"level": st["from_level"], "position": st["bottom"]},
                {"level": st["to_level"], "position": st["top"]}, 2.0, st.get("direction", "both") == "both")
               for st in stairs]
    out = []
    for lid, kind, fa, fb, mult, both in raw:
        if fa["level"] not in lv or fb["level"] not in lv:
            continue
        a, b = np.asarray(fa["position"], float), np.asarray(fb["position"], float)
        la, lb = lv[fa["level"]], lv[fb["level"]]
        dy = abs(nav.elev[la] - nav.elev[lb])
        d2 = float(np.linalg.norm(a - b))
        length = max(math.hypot(d2, dy), 2.0 * dy)  # stairs are about 30 degrees
        st = next((s for s in stairs if {s["from_level"], s["to_level"]} == {fa["level"], fb["level"]}
                   and min(np.linalg.norm(np.asarray(s["bottom"]) - a), np.linalg.norm(np.asarray(s["top"]) - a)) < 0.75),
                  None)
        sid, width = (st["id"], float(st["width_m"])) if st else (lid, DEFAULT_STAIR_WIDTH_M)
        out.append(Link(sid, kind, a, b, la, lb, length, mult, nav.elev[la] > nav.elev[lb], width))
        if both:
            out.append(Link(sid, kind, b, a, lb, la, length, mult, nav.elev[lb] > nav.elev[la], width))
    return out


def _door_zone(scene: dict, level: str) -> object:
    """Area within DOOR_ZONE_M of any door segment on a level (where refinement happens)."""
    segs = [LineString(d["segment"]) for d in scene.get("doors", []) if d["level"] == level]
    zone = unary_union(segs).buffer(DOOR_ZONE_M) if segs else Polygon()
    shapely.prepare(zone)
    return zone


def refine(verts: np.ndarray, tris: np.ndarray, tags: np.ndarray, max_edge: float,
           zones: list | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split edges longer than max_edge (m) that touch a door zone (per tag/level) until none remain.

    Splits are decided per edge, so both triangles sharing an edge agree and the
    mesh stays conforming (no T-junctions). With zones=None every long edge splits.
    """
    vs = [tuple(v) for v in verts]
    ts = [tuple(int(i) for i in t) for t in tris]
    tg = [int(x) for x in tags]
    for _ in range(REFINE_PASSES):
        cand: dict[tuple[int, int], int] = {}
        for t, g in zip(ts, tg):
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                k = (min(a, b), max(a, b))
                if k not in cand and math.dist(vs[a], vs[b]) > max_edge:
                    cand[k] = g
        keys = list(cand)
        if zones is not None and keys:
            lines = shapely.linestrings([[vs[a], vs[b]] for a, b in keys])
            tag = np.array([cand[k] for k in keys])
            keep = np.zeros(len(keys), dtype=bool)
            for g in np.unique(tag):
                rows = tag == g
                keep[rows] = shapely.intersects(zones[g], lines[rows])
            keys = [k for k, ok in zip(keys, keep) if ok]
        if not keys:
            break
        mid: dict[tuple[int, int], int] = {}
        for a, b in keys:
            mid[(a, b)] = len(vs)
            vs.append(((vs[a][0] + vs[b][0]) / 2, (vs[a][1] + vs[b][1]) / 2))
        kids = [(nt, g) for t, g in zip(ts, tg) for nt in _split(t, mid)]
        ts, tg = [k[0] for k in kids], [k[1] for k in kids]
    return np.asarray(vs, dtype=float), np.asarray(ts, dtype=int), np.asarray(tg, dtype=int)


def _split(t: tuple[int, int, int], mid: dict[tuple[int, int], int]) -> list[tuple[int, int, int]]:
    """Children of triangle t given the midpoints of its split edges (orientation kept)."""
    def m(a: int, b: int) -> int | None:
        return mid.get((min(a, b), max(a, b)))

    for r in range(3):  # rotate so the split pattern is canonical
        a, b, c = t[r], t[(r + 1) % 3], t[(r + 2) % 3]
        ab, bc, ca = m(a, b), m(b, c), m(c, a)
        if ab is not None and bc is not None and ca is not None:
            return [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
        if ab is not None and bc is not None and ca is None:
            return [(ab, b, bc), (a, ab, bc), (a, bc, c)]
        if ab is not None and bc is None and ca is None:
            return [(a, ab, c), (ab, b, c)]
    return [t]


def _cross(u: np.ndarray, v: np.ndarray) -> float:
    return float(u[0] * v[1] - u[1] * v[0])


def _area2(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Twice the signed area of abc (Mononen's convention)."""
    return float((c[0] - a[0]) * (b[1] - a[1]) - (b[0] - a[0]) * (c[1] - a[1]))


def _same(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 < 1e-12


def _string_pull(portals: list[tuple[np.ndarray, np.ndarray]]) -> list[np.ndarray]:
    """Mononen's simple stupid funnel over (left, right) portals; returns waypoints."""
    portals = [((float(l[0]), float(l[1])), (float(r[0]), float(r[1]))) for l, r in portals]  # plain floats: 5x faster
    apex, left, right = portals[0][0], portals[0][0], portals[0][1]
    apex_i = left_i = right_i = 0
    pts = [apex]
    i = 1
    while i < len(portals):
        pl, pr = portals[i]
        if _area2(apex, right, pr) <= 0.0:
            if _same(apex, right) or _area2(apex, left, pr) > 0.0:
                right, right_i = pr, i
            else:
                apex, apex_i = left, left_i
                pts.append(apex)
                left = right = apex
                left_i = right_i = apex_i
                i = apex_i + 1
                continue
        if _area2(apex, left, pl) >= 0.0:
            if _same(apex, left) or _area2(apex, right, pl) < 0.0:
                left, left_i = pl, i
            else:
                apex, apex_i = right, right_i
                pts.append(apex)
                left = right = apex
                left_i = right_i = apex_i
                i = apex_i + 1
                continue
        i += 1
    if not _same(pts[-1], portals[-1][0]):
        pts.append(portals[-1][0])
    return [np.asarray(p, dtype=float) for p in pts]
