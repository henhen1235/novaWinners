"""Ground-truth scene plus synthetic scan from one storey of an IFC file.

Spaces become rooms, doors become door segments; tools.stress_scans.build_truth
derives walls, walkable and navmesh, and tools.make_synthetic_scan.make_scan
writes the scan. IFC is Z up; scene 2D is [x, z] = [x_ifc, -y_ifc].

    python -m tools.ifc_to_scene data/external/ifc_duplex/Duplex_A_20110907.ifc -o data/stress/ifc_duplex
"""

from __future__ import annotations

import argparse
import multiprocessing
import time
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import networkx as nx
import numpy as np
import shapely
import shapely.ops
from shapely.geometry import Point, Polygon

from tools.make_synthetic_scan import make_scan
from tools.stress_scans import build_truth

T = 0.15
PROBE = T / 2 + 0.1  # the builder's side probe distance, mirrored so we can pre-check doors
MIN_AREA = 1.0
DOOR_W = (0.6, 2.2)
# Contract room types keyed by words in IfcSpace.LongName; first match wins, else "unknown".
TYPES = [("stair", "stairwell"), ("bath", "restroom"), ("toilet", "restroom"), ("restroom", "restroom"),
         ("wc", "restroom"), ("corr", "corridor"), ("hall", "corridor"), ("lobby", "lobby"), ("foyer", "lobby"),
         ("entr", "lobby"), ("wait", "lobby"), ("stor", "storage"), ("closet", "storage"), ("mech", "mechanical"),
         ("elec", "mechanical"), ("office", "office"), ("class", "classroom")]


def _type(rid: str) -> str:
    low = rid.lower()
    return next((t for key, t in TYPES if key in low), "unknown")


def _meshes(model: ifcopenshell.file, products: list) -> dict[int, np.ndarray]:
    """id -> (n, 3, 3) world-frame triangles, already mapped to scene axes (x, z_scene, y_up)."""
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    out: dict[int, np.ndarray] = {}
    it = ifcopenshell.geom.iterator(settings, model, multiprocessing.cpu_count(), include=products)
    if not it.initialize():
        return out
    while True:
        shape = it.get()
        v = np.asarray(shape.geometry.verts, float).reshape(-1, 3)
        f = np.asarray(shape.geometry.faces, int).reshape(-1, 3)
        out[shape.id] = np.stack([v[:, 0], -v[:, 1], v[:, 2]], axis=1)[f]
        if not it.next():
            return out


def _footprint(tris: np.ndarray) -> Polygon | None:
    """Largest polygon of the union of the triangles projected to the floor plane."""
    polys = [Polygon(t[:, :2]) for t in tris]
    flat = shapely.union_all([p for p in polys if p.is_valid and p.area > 1e-6]).buffer(0)
    parts = sorted(getattr(flat, "geoms", [flat]), key=lambda p: p.area)
    if not parts or parts[-1].is_empty:
        return None
    # Holes would give the builder interior rings it does not wall, so keep the outline only.
    poly = Polygon(parts[-1].exterior).simplify(0.02)
    return poly if poly.is_valid and poly.area >= MIN_AREA else None


def _door_segment(tris: np.ndarray, width: float) -> list[list[float]]:
    """Segment through the door's plan centre along its principal horizontal axis.

    PCA rather than an axis-aligned bbox so doors in rotated walls keep their direction.
    """
    xy = tris.reshape(-1, 3)[:, :2]
    mean = xy.mean(axis=0)
    _, _, vt = np.linalg.svd(xy - mean, full_matrices=False)
    u = vt[0] * np.sign(vt[0][np.argmax(np.abs(vt[0]))])  # sign fixed for determinism
    along, across = (xy - mean) @ u, (xy - mean) @ vt[1]
    c = mean + u * (along.max() + along.min()) / 2 + vt[1] * (across.max() + across.min()) / 2
    half = np.clip(width, *DOOR_W) / 2
    return [(c - u * half).round(4).tolist(), (c + u * half).round(4).tolist()]


def _snap(seg: list, rooms: dict[str, Polygon]) -> list:
    """Shift a door across its wall so the builder's probes land in the rooms.

    IFC door meshes sit on the wall centreline, but walls here can be thicker than
    the probe reach. Two rooms: centre between their faces. One room (exit): put
    the segment on that room's grown edge, t/2 outside its face, as build_truth expects.
    """
    p0, p1 = np.asarray(seg, float)
    d = (p1 - p0) / np.linalg.norm(p1 - p0)
    n, c = np.array([-d[1], d[0]]), (p0 + p1) / 2
    near: dict[int, float] = {}
    for poly in rooms.values():
        if poly.distance(Point(c)) > 0.6:
            continue
        off = float(np.dot(np.asarray(shapely.ops.nearest_points(poly.boundary, Point(c))[0].coords[0]) - c, n))
        side = 1 if off > 0 else -1
        if abs(off) < abs(near.get(side, np.inf)):
            near[side] = off
    if len(near) == 2:
        shift = (near[1] + near[-1]) / 2
    elif len(near) == 1:
        (side, off), = near.items()
        shift = off - side * T / 2
    else:
        return seg
    return [(p0 + n * shift).round(4).tolist(), (p1 + n * shift).round(4).tolist()]


def _merge_open(rooms: dict[str, Polygon]) -> dict[str, Polygon]:
    """Union spaces that touch along > 0.6 m with no wall between (open plan).

    The builder walls every room edge, so an open living/kitchen boundary would
    otherwise become a solid wall and strand one of the rooms.
    """
    g = nx.Graph()
    g.add_nodes_from(rooms)
    ids = list(rooms)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if rooms[a].distance(rooms[b]) < 0.02:
                shared = rooms[a].buffer(0.02).intersection(rooms[b].buffer(0.02)).area / 0.04
                if shared > 0.6:
                    g.add_edge(a, b)
    out = {}
    for comp in sorted(sorted(c) for c in nx.connected_components(g)):
        merged = shapely.union_all([rooms[r].buffer(0.02, join_style="mitre") for r in comp]).buffer(-0.02, join_style="mitre")
        out["+".join(comp)] = max(getattr(merged, "geoms", [merged]), key=lambda p: p.area).simplify(0.02)
    return out


def _sides(seg: list, interiors: dict[str, Polygon]) -> list[str]:
    """Rooms on each side of a door, as the builder will find them."""
    p0, p1 = np.asarray(seg, float)
    d = p1 - p0
    n = np.array([-d[1], d[0]]) / np.linalg.norm(d)
    mid = (p0 + p1) / 2
    return [next((rid for rid, ip in interiors.items() if ip.covers(Point(mid + s * n * PROBE))), "outside")
            for s in (-1, 1)]


def _storey(model: ifcopenshell.file, name: str | None) -> ifcopenshell.entity_instance:
    """Named storey, else the lowest storey that has spaces (the ground floor)."""
    storeys = model.by_type("IfcBuildingStorey")
    if name:
        return next(s for s in storeys if s.Name == name)
    with_spaces = [s for s in storeys if any(o.is_a("IfcSpace") for r in s.IsDecomposedBy for o in r.RelatedObjects)]
    return min(with_spaces, key=lambda s: s.Elevation)


def convert(ifc: Path, storey_name: str | None = None) -> tuple[dict, dict]:
    """(truth scene, stats) for one storey. Drops what the builder could not accept."""
    model = ifcopenshell.open(str(ifc))
    storey = _storey(model, storey_name)
    spaces = [o for r in storey.IsDecomposedBy for o in r.RelatedObjects if o.is_a("IfcSpace")]
    doors = [e for r in storey.ContainsElements for e in r.RelatedElements if e.is_a("IfcDoor")]
    meshes = _meshes(model, spaces + doors)
    stats: dict = {"storey": storey.Name, "ifc_spaces": len(spaces), "ifc_doors": len(doors), "dropped": []}

    rooms: dict[str, Polygon] = {}
    for sp in sorted(spaces, key=lambda s: s.GlobalId):
        poly = _footprint(meshes[sp.id()]) if sp.id() in meshes else None
        if poly is None:
            stats["dropped"].append(f"space {sp.Name}: no footprint or area < {MIN_AREA} m^2")
            continue
        rid = f"{sp.Name}_{sp.LongName or ''}".strip("_").replace(" ", "_")
        # IFC spaces overlap a little at shared walls; the first space keeps the overlap.
        clipped = poly.difference(shapely.union_all(list(rooms.values()))) if rooms else poly
        best = max(getattr(clipped, "geoms", [clipped]), key=lambda p: p.area)
        if best.area < MIN_AREA or not isinstance(best, Polygon):
            stats["dropped"].append(f"space {sp.Name}: overlaps another space")
            continue
        rooms[rid] = best

    merged = _merge_open(rooms)
    stats["merged_open_plan"] = [k for k in merged if "+" in k]
    rooms = merged
    segs: list[tuple[str, list]] = []
    for dr in sorted(doors, key=lambda d: d.GlobalId):
        if dr.id() not in meshes or not dr.OverallWidth:
            stats["dropped"].append(f"door {dr.Name}: no geometry or width")
            continue
        segs.append((dr.Name, _snap(_door_segment(meshes[dr.id()], float(dr.OverallWidth)), rooms)))

    # Interior = grown footprint shrunk back by t/2 = footprint, so probe footprints directly.
    while True:
        kept, graph = [], nx.Graph()
        graph.add_nodes_from(list(rooms) + ["outside"])
        for name, seg in segs:
            a, b = _sides(seg, rooms)
            if a == b:
                stats["dropped"].append(f"door {name}: {a} on both sides")
                continue
            kept.append((name, seg, a, b))
            graph.add_edge(a, b)
        reach = nx.node_connected_component(graph, "outside")
        lost = sorted(set(rooms) - reach)
        if not lost:
            break
        stats["dropped"] += [f"room {r}: not reachable from an exit" for r in lost]
        rooms = {k: v for k, v in rooms.items() if k not in lost}
        segs = [(n, s) for n, s, *_ in kept]

    stats.update(rooms=len(rooms), doors=len(kept), exits=sum("outside" in (a, b) for *_, a, b in kept))
    room_list = [(rid, _type(rid), poly.buffer(T / 2, join_style="mitre")) for rid, poly in rooms.items()]
    door_list = [(seg, f"Exit {name}" if "outside" in (a, b) else None) for name, seg, a, b in kept]
    return build_truth(f"ifc_{ifc.stem}", room_list, door_list, ceiling=3.0, t=T), stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ifc", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--storey", help="IfcBuildingStorey.Name; default the lowest storey with spaces")
    a = ap.parse_args(argv)
    start = time.perf_counter()
    truth, stats = convert(a.ifc, a.storey)
    make_scan(truth, seed=7, out_dir=a.out)
    for line in stats.pop("dropped"):
        print("dropped", line)
    print(stats, f"{time.perf_counter() - start:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
