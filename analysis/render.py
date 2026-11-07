"""Draw the analysis on top of tools/render_plan.py's plan SVG.

render_plan has no overlay hook, so this repeats its per-level transform
(same constants and _bounds) and appends one <g class="analysis"> before </svg>.
Red: single-point-of-failure doors and rooms. Amber: route pinch points.
Room labels gain the smoke clock.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from tools.render_plan import GAP, MARGIN, SIGN_PAD, TOKENS, _bounds, _path, render

RED, AMBER, INK = TOKENS["color"]["hazard"], TOKENS["color"]["warn"], TOKENS["plan"]["ink"]


def _transforms(scene: dict) -> dict:
    boxes = [(lv["id"], _bounds(scene, lv["id"])) for lv in scene["levels"]]
    s = min(48.0, 1150 / sum(b[2] - b[0] for _, b in boxes))
    out, x_off = {}, MARGIN
    for lid, (x0, z0, x1, _) in boxes:
        out[lid] = lambda p, ox=x_off + SIGN_PAD, bx=x0, bz=z0: ((p[0] - bx) * s + ox, (p[1] - bz) * s + MARGIN + 56)
        x_off += (x1 - x0) * s + GAP + 2 * SIGN_PAD
    return out


def overlay(scene: dict, analysis: dict) -> str:
    tx = _transforms(scene)
    rooms = {r["id"]: r for r in scene["rooms"]}
    doors = {d["id"]: d for d in scene["doors"]}
    parts = []
    for row in analysis["spof"]["ranked"]:
        if not row["stranded_rooms"] or row["leaf"]:
            continue
        n = len(row["stranded_rooms"])
        if row["kind"] == "door" and row["id"] in doors:
            d = doors[row["id"]]
            (a, b) = [tx[d["level"]](p) for p in d["segment"]]
            parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="{RED}" '
                         f'stroke-width="6" stroke-linecap="round"><title>SPOF {row["id"]}: strands {n} rooms</title></line>')
        elif row["kind"] == "room" and row["id"] in rooms:
            r = rooms[row["id"]]
            t = tx[r["level"]]
            x, y = t(r["centroid"])
            parts.append(f'<path d="{_path(r["polygon"], t)}" fill="none" stroke="{RED}" stroke-width="3" '
                         f'stroke-dasharray="8 4"/><text x="{x:.1f}" y="{y - 18:.1f}" class="spof">'
                         f'SINGLE POINT OF FAILURE: strands {n} room{"s" * (n != 1)}</text>')
    seen = set()
    for row in analysis["pinch"]["rooms"]:
        if "pinch_at" not in row or row["pinch_from"] != "clearance raster":
            continue
        key = tuple(round(v, 1) for v in row["pinch_at"])
        if key in seen:
            continue
        seen.add(key)
        x, y = tx[rooms[row["room"]]["level"]](row["pinch_at"])
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{AMBER}" stroke="{INK}" stroke-width="1"/>'
                     f'<text x="{x + 10:.1f}" y="{y + 4:.1f}" class="pinch">{row["pinch_width_m"]:.2f} m, '
                     f'{row["flow_p_per_min"]} p/min</text>')
    for rid, sm in analysis["smoke"]["rooms"].items():
        x, y = tx[rooms[rid]["level"]](rooms[rid]["centroid"])
        parts.append(f'<text x="{x:.1f}" y="{y + 28:.1f}" class="smoke">smoke at 1.8 m: {sm["t_head_height_s"]:.0f} s</text>')
    style = (f".spof{{font:600 11px sans-serif;fill:{RED};text-anchor:middle}}"
             f".pinch{{font:600 11px monospace;fill:{INK};paint-order:stroke;stroke:#FFF;stroke-width:3px}}"
             f".smoke{{font:500 10px monospace;fill:{RED};text-anchor:middle;paint-order:stroke;stroke:#FFF;stroke-width:3px}}")
    note = escape("Red: single points of failure. Amber: narrowest clear width on a room's widest route. "
                  "Smoke times are estimates (fast t^2 fire, room of origin).")
    return (f'<g class="analysis"><style>{style}</style>{"".join(parts)}'
            f'<text x="{MARGIN}" y="{MARGIN + 34}" class="pinch">{note}</text></g>')


def render_with_analysis(scene: dict, analysis: dict) -> str:
    svg = render(scene)
    i = svg.rindex("</svg>")
    return svg[:i] + overlay(scene, analysis) + svg[i:]
