"""Navmesh graph, point location, A*, funnel and exit distance fields.

All distances in meters. Points are [x, z]. Triangles touching along a shared
edge are neighbours; vertices are merged at 1 mm so separately indexed copies
still connect.
"""

from __future__ import annotations

import heapq
import math

import numpy as np
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

INF = math.inf
MAX_EDGE_M = 2.0  # refine coarse meshes so door and room hazards stay local


class NavMesh:
    """Triangle graph of one level. `mult` holds per-triangle entry cost multipliers."""

    def __init__(self, scene: dict, level: str | None = None) -> None:
        nav = scene["navmesh"]
        self.level = level or scene["levels"][0]["id"]
        keep = [i for i, lv in enumerate(nav["triangle_level"]) if lv == self.level]
        raw = np.asarray(nav["vertices"], dtype=float)[:, [0, 2]]
        key = np.round(raw, 3)
        _, inverse = np.unique(key, axis=0, return_inverse=True)
        uniq = np.zeros((inverse.max() + 1, 2))
        uniq[inverse.ravel()] = raw
        self.verts = uniq
        self.tris = np.asarray(nav["triangles"], dtype=int)[keep]
        self.tris = inverse.ravel()[self.tris]
        self.verts, self.tris = refine(self.verts, self.tris, MAX_EDGE_M)
        self.centroids = self.verts[self.tris].mean(axis=1)
        self.polys = [Polygon(self.verts[t]) for t in self.tris]
        self.tree = shapely.STRtree(self.polys)
        self._build_adjacency()
        polys = [Polygon(p["exterior"], p["holes"]) for p in scene["walkable"][self.level]["polygons"]]
        self.walk = unary_union(polys)
        self.walk_los = self.walk.buffer(0.05)
        self.walk_move = self.walk.buffer(0.02)  # funnel paths run along edges; absorb float error
        shapely.prepare(self.walk)
        shapely.prepare(self.walk_move)
        shapely.prepare(self.walk_los)
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
        pairs = np.array([p for p in self.portals], dtype=int).reshape(-1, 2)
        self.edge_src, self.edge_dst = pairs[:, 0], pairs[:, 1]
        self.edge_len = np.linalg.norm(self.centroids[self.edge_src] - self.centroids[self.edge_dst], axis=1)
        self.edge_len = np.maximum(self.edge_len, 1e-4)

    # ---- point queries -------------------------------------------------
    def locate(self, pts: np.ndarray) -> np.ndarray:
        """Triangle index for each [x, z] point; nearest triangle when outside the mesh."""
        pts = np.atleast_2d(pts)
        geoms = shapely.points(pts)
        out = np.full(len(pts), -1, dtype=int)
        hit_pt, hit_tri = self.tree.query(geoms, predicate="intersects")
        out[hit_pt[::-1]] = hit_tri[::-1]  # first hit wins
        miss = np.flatnonzero(out < 0)
        if len(miss):
            near = self.tree.query_nearest(geoms[miss], return_distance=False, all_matches=False)
            out[miss[near[0]]] = near[1]
        return out

    def is_walkable(self, pts: np.ndarray) -> np.ndarray:
        """Bool per [x, z] point: inside the walkable area (2 cm tolerance)."""
        pts = np.atleast_2d(pts)
        return shapely.intersects_xy(self.walk_move, pts[:, 0], pts[:, 1])

    def snap(self, pt: list[float] | np.ndarray, inset_m: float = 0.02) -> np.ndarray:
        """Nearest walkable point to `pt`, pulled `inset_m` inward when it was outside."""
        p = Point(pt)
        if self.walk.contains(p):
            return np.asarray(pt, dtype=float)
        q = np.array(nearest_points(self.walk, p)[0].coords[0])
        c = self.centroids[self.locate(q[None])[0]]
        d = c - q
        n = np.linalg.norm(d)
        return q + d / n * min(inset_m, n) if n > 0 else q

    def line_of_sight(self, src: np.ndarray, dsts: np.ndarray) -> np.ndarray:
        """Bool per destination: segment src->dst stays on (slightly buffered) walkable."""
        dsts = np.atleast_2d(dsts)
        if len(dsts) == 0:
            return np.zeros(0, dtype=bool)
        coords = np.stack([np.broadcast_to(src, dsts.shape), dsts], axis=1)
        same = np.all(np.abs(coords[:, 0] - coords[:, 1]) < 1e-6, axis=1)
        coords[same, 1] += 1e-4
        return shapely.covers(self.walk_los, shapely.linestrings(coords))

    def clear_segments(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Bool per row: straight move a[i] -> b[i] stays walkable (same 2 cm tolerance as moves)."""
        if len(a) == 0:
            return np.zeros(0, dtype=bool)
        coords = np.stack([a, b], axis=1).copy()
        same = np.all(np.abs(coords[:, 0] - coords[:, 1]) < 1e-6, axis=1)
        coords[same, 1] += 1e-4
        return shapely.covers(self.walk_move, shapely.linestrings(coords))

    # ---- search --------------------------------------------------------
    def step_cost(self, i: int, j: int) -> float:
        """Cost of moving from triangle i into neighbour j (meters times j's multiplier)."""
        return float(np.linalg.norm(self.centroids[i] - self.centroids[j])) * self.mult[j]

    def astar(self, start: int, goal: int) -> list[int] | None:
        """Triangle corridor from start to goal over centroids, or None when unreachable."""
        h = lambda t: float(np.linalg.norm(self.centroids[t] - self.centroids[goal]))  # noqa: E731
        best = {start: 0.0}
        came: dict[int, int] = {}
        heap = [(h(start), start)]
        while heap:
            _, cur = heapq.heappop(heap)
            if cur == goal:
                path = [cur]
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                return path[::-1]
            for nb in self.neighbors[cur]:
                g = best[cur] + self.step_cost(cur, nb)
                if g < best.get(nb, INF):
                    best[nb], came[nb] = g, cur
                    heapq.heappush(heap, (g + h(nb), nb))
        return None

    def distance_field(self, seed: int) -> tuple[np.ndarray, np.ndarray]:
        """Cost-to-go from every triangle to `seed` and the next triangle toward it (-1 at seed)."""
        w = self.edge_len * self.mult[self.edge_dst]
        ok = np.isfinite(w)
        n = len(self.tris)
        # Reverse edges: distances from seed in the transpose are distances to seed.
        g = csr_matrix((w[ok], (self.edge_dst[ok], self.edge_src[ok])), shape=(n, n))
        dist, pred = dijkstra(g, indices=seed, return_predecessors=True)
        return dist, pred

    def corridor(self, start: int, nxt: np.ndarray) -> list[int]:
        """Follow a field's next-triangle pointers from start to the seed."""
        path = [start]
        while nxt[path[-1]] >= 0 and len(path) <= len(self.tris):
            path.append(int(nxt[path[-1]]))
        return path

    def funnel(self, start: np.ndarray, goal: np.ndarray, corridor: list[int]) -> list[np.ndarray]:
        """String-pull a triangle corridor into waypoints (simple stupid funnel algorithm)."""
        portals = [(start, start)]
        for a, b in zip(corridor[:-1], corridor[1:]):
            pa, pb = self.portals[(a, b)]
            va, vb = self.verts[pa], self.verts[pb]
            facing = (va + vb) / 2 - self.centroids[a]
            right_is_a = _cross(facing, va - self.centroids[a]) < 0
            portals.append((vb, va) if right_is_a else (va, vb))  # (left, right)
        portals.append((goal, goal))
        return _string_pull(portals)

    def find_path(self, a: np.ndarray, b: np.ndarray) -> list[np.ndarray] | None:
        """Smooth waypoints from a to b (A* corridor + funnel), or None when unreachable."""
        ta, tb = self.locate(np.array([a, b]))
        corr = self.astar(int(ta), int(tb))
        return None if corr is None else self.funnel(np.asarray(a, float), np.asarray(b, float), corr)

    def tris_in(self, geom: Polygon | LineString) -> np.ndarray:
        """Triangles whose area intersects `geom` (a room polygon or door segment)."""
        return self.tree.query(geom, predicate="intersects")

    def tris_with_centroid_in(self, poly: Polygon) -> np.ndarray:
        """Triangles whose centroid lies inside `poly`."""
        return np.flatnonzero(shapely.contains_xy(poly, self.centroids[:, 0], self.centroids[:, 1]))


def refine(verts: np.ndarray, tris: np.ndarray, max_edge: float) -> tuple[np.ndarray, np.ndarray]:
    """Split every edge longer than max_edge (m) at its midpoint until none remain.

    Splits are decided per edge, so both triangles sharing an edge agree and the
    mesh stays conforming (no T-junctions).
    """
    vs = [tuple(v) for v in verts]
    ts = [tuple(int(i) for i in t) for t in tris]
    for _ in range(12):
        mid: dict[tuple[int, int], int] = {}
        for t in ts:
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                k = (min(a, b), max(a, b))
                if k not in mid and math.dist(vs[a], vs[b]) > max_edge:
                    mid[k] = len(vs)
                    vs.append(((vs[a][0] + vs[b][0]) / 2, (vs[a][1] + vs[b][1]) / 2))
        if not mid:
            break
        ts = [nt for t in ts for nt in _split(t, mid)]
    return np.asarray(vs, dtype=float), np.asarray(ts, dtype=int)


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


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    return float(np.sum((a - b) ** 2)) < 1e-12


def _string_pull(portals: list[tuple[np.ndarray, np.ndarray]]) -> list[np.ndarray]:
    """Mononen's simple stupid funnel over (left, right) portals; returns waypoints."""
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
