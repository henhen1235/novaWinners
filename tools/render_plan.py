"""Render scene.json as a presentation-grade top-down plan (SVG).

Usage: python -m tools.render_plan out/view/scene.json -o out/view/plan.svg

One panel per level: rooms tinted by type with name and area, walls as
solid poché, doors with swing arcs where opens_toward is known, exits in
ISO 7010 green with the running-figure sign, obstacles, points of interest,
the walkable boundary (dashed), a meter scale bar, a plan-north arrow and a
title block with the validator status. Colors and fonts come from
design/tokens.json. Writes a PNG next to the SVG only if cairosvg is
installed. Plan frame: x right, z down (matches the viewer's Top view).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

TOKENS = json.loads((Path(__file__).resolve().parents[1] / "design" / "tokens.json").read_text())
C, P, F = TOKENS["color"], TOKENS["plan"], TOKENS["font"]
FONT_IMPORT = ("https://fonts.googleapis.com/css2?family=Barlow:wght@400;600"
               "&family=Barlow+Condensed:wght@500;600&family=IBM+Plex+Mono:wght@400;500&display=swap")
MARGIN, GAP, STRIP = 56, 64, 150  # px: page margin, gap between level panels, bottom strip height
SIGN_PAD = 140  # px each side of a panel, room for exit signs pushed outside the walls
POI = {  # kind: (code, fill, legend text); red is fire equipment, green is safe condition (ISO 7010)
    "fire_extinguisher": ("FE", C["hazard"], "Fire extinguisher"),
    "alarm_pull": ("FA", C["hazard"], "Fire alarm call point"),
    "sprinkler": ("SP", C["hazard"], "Sprinkler"),
    "fire_exit_sign": ("EX", C["exit"], "Exit sign"),
    "assembly_point": ("AP", C["exit"], "Assembly point"),
    "aed": ("AED", C["exit"], "Defibrillator"),
}
# Same figure as the viewer's exit sign, in a 24 x 24 box.
RUNNER = ('<circle cx="14.5" cy="4" r="2.2" fill="#FFF"/><path d="M6.5 10 9.5 7.6 13.5 7.5 16 11 19 11.5'
          'M13.5 7.5 10.5 14M4.5 20 8.5 17.5 10.5 14 14 16.5 13.5 21.5" fill="none" stroke="#FFF" '
          'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>')


def _inside(pt: list[float], ring: list[list[float]]) -> bool:
    """Even-odd point-in-polygon test."""
    x, z = pt
    hit = False
    for (x1, z1), (x2, z2) in zip(ring, ring[1:] + ring[:1]):
        if (z1 > z) != (z2 > z) and x < x1 + (z - z1) * (x2 - x1) / (z2 - z1):
            hit = not hit
    return hit


def _path(ring: list[list[float]], tx) -> str:
    return "M" + " L".join(f"{tx(p)[0]:.1f} {tx(p)[1]:.1f}" for p in ring) + " Z"


def _side_normal(door: dict, rooms: dict, toward: str) -> tuple[float, float] | None:
    """Unit normal of the door segment that points into `toward` (a room id or "outside")."""
    (x0, z0), (x1, z1) = door["segment"]
    length = math.hypot(x1 - x0, z1 - z0) or 1.0
    normal = (-(z1 - z0) / length, (x1 - x0) / length)
    mid = ((x0 + x1) / 2, (z0 + z1) / 2)
    probe = [mid[0] + normal[0] * 0.3, mid[1] + normal[1] * 0.3]
    if toward == "outside":
        others = [rooms[r]["polygon"] for r in door["connects"] if r in rooms]
        on_normal_side = not any(_inside(probe, ring) for ring in others)
    elif toward in rooms:
        on_normal_side = _inside(probe, rooms[toward]["polygon"])
    else:
        return None
    return normal if on_normal_side else (-normal[0], -normal[1])


def _door_svg(door: dict, rooms: dict, tx, s: float) -> str:
    color = C["exit"] if door["is_exit"] else P["ink"]
    a, b = door["segment"]
    out = [f'<line x1="{tx(a)[0]:.1f}" y1="{tx(a)[1]:.1f}" x2="{tx(b)[0]:.1f}" y2="{tx(b)[1]:.1f}" '
           f'stroke="{color}" stroke-width="{2.5 if door["is_exit"] else 1}"/>']
    normal = _side_normal(door, rooms, door["opens_toward"]) if door.get("opens_toward") else None
    if normal is None or door["kind"] not in ("swing", "double"):
        return "".join(out)
    hinges = [(a, b), (b, a)] if door["kind"] == "double" else [(a, b)]
    for hinge, latch in hinges:
        width = math.dist(a, b) / len(hinges)
        d = ((latch[0] - hinge[0]) / math.dist(a, b), (latch[1] - hinge[1]) / math.dist(a, b))
        tip = (hinge[0] + normal[0] * width, hinge[1] + normal[1] * width)
        end = (hinge[0] + d[0] * width, hinge[1] + d[1] * width)
        sweep = 1 if normal[0] * d[1] - normal[1] * d[0] > 0 else 0
        h, t, e = tx(hinge), tx(tip), tx(end)
        out.append(f'<path d="M{h[0]:.1f} {h[1]:.1f} L{t[0]:.1f} {t[1]:.1f} A{width * s:.1f} {width * s:.1f} 0 0 '
                   f'{sweep} {e[0]:.1f} {e[1]:.1f}" fill="none" stroke="{color}" stroke-width="1"/>')
    return "".join(out)


def _exit_svg(exit_: dict, door: dict | None, rooms: dict, tx) -> str:
    """Green sign with the running figure, pushed outside the building when the side is known."""
    x, y = tx(exit_["position"])
    label = escape(exit_["label"])
    width = 34 + 7.2 * len(exit_["label"])
    normal = _side_normal(door, rooms, "outside") if door else None
    if normal:  # clear the wall by 16 px plus half the sign along the outward normal
        push = 16 + abs(normal[0]) * width / 2 + abs(normal[1]) * 13
        x, y = x + normal[0] * push, y + normal[1] * push
    return (f'<g class="exit" data-id={quoteattr(exit_["id"])} transform="translate({x - width / 2:.1f} {y - 13:.1f})">'
            f'<rect width="{width:.1f}" height="26" rx="2" fill="{C["exit"]}"/>'
            f'<g transform="translate(5 3) scale(0.83)">{RUNNER}</g>'
            f'<text x="28" y="18" class="sign">{label}</text></g>')


def _poi_svg(poi: dict, tx) -> str:
    code, fill, _ = POI[poi["kind"]]
    x, y = tx(poi["position"])
    w = 12 + 6 * len(code)
    return (f'<g class="poi" transform="translate({x - w / 2:.1f} {y - 9:.1f})"><rect width="{w}" height="18" rx="2" '
            f'fill="{fill}"/><text x="{w / 2}" y="13" class="code">{code}</text></g>')


def _level_svg(scene: dict, level: dict, rooms: dict, tx, s: float) -> str:
    lid = level["id"]
    on = lambda items: [i for i in items if i.get("level") == lid]  # noqa: E731
    parts = []
    for room in on(scene["rooms"]):
        fill = P.get(f"room-{room['type']}", P["room-unknown"])
        parts.append(f'<path d="{_path(room["polygon"], tx)}" fill="{fill}"/>')
    for poly in scene["walkable"].get(lid, {}).get("polygons", []):
        for ring in [poly["exterior"], *poly.get("holes", [])]:
            parts.append(f'<path d="{_path(ring, tx)}" fill="none" stroke="{P["walkable"]}" '
                         'stroke-width="0.8" stroke-dasharray="4 3"/>')
    for obstacle in on(scene.get("obstacles", [])):
        parts.append(f'<path d="{_path(obstacle["polygon"], tx)}" fill="{P["obstacle"]}" stroke="{P["line"]}" stroke-width="0.6"/>')
    for wall in on(scene["walls"]):
        parts.append(f'<path d="{_path(wall["polygon"], tx)}" fill="{P["ink"]}"/>')
    for stair in [st for st in scene.get("stairs", []) if lid in (st["from_level"], st["to_level"])]:
        parts.append(f'<path d="{_path(stair["polygon"], tx)}" fill="none" stroke="{P["ink"]}" stroke-width="1"/>')
    doors = {d["id"]: d for d in scene["doors"]}
    parts += [_door_svg(d, rooms, tx, s) for d in on(scene["doors"])]
    for room in on(scene["rooms"]):
        x, y = tx(room["centroid"])
        parts.append(f'<g class="room" data-id={quoteattr(room["id"])}><text x="{x:.1f}" y="{y:.1f}" class="name">'
                     f'{escape(room["name"])}</text><text x="{x:.1f}" y="{y + 14:.1f}" class="area">'
                     f'{room["area_m2"]:.1f} m²</text></g>')
    parts += [_poi_svg(p, tx) for p in on(scene.get("points_of_interest", [])) if p["kind"] in POI]
    parts += [_exit_svg(e, doors.get(e["door_id"]), rooms, tx) for e in on(scene["exits"])]
    return "".join(parts)


def _bounds(scene: dict, lid: str) -> tuple[float, float, float, float]:
    pts = [p for key in ("walls", "rooms") for item in scene[key] if item["level"] == lid for p in item["polygon"]]
    pts += [e["position"] for e in scene["exits"] if e["level"] == lid]
    xs, zs = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs) - 1.5, min(zs) - 1.5, max(xs) + 1.5, max(zs) + 1.5


def _status(scene: dict) -> tuple[str, bool]:
    try:
        from contract.validate_scene import validate
        issues = validate(scene)
    except ImportError:  # ponytail: contract deps missing, fall back to the scene's own warnings
        issues = scene.get("warnings", [])
    errors = sum(i["severity"] == "error" for i in issues)
    warns = len(issues) - errors
    return f"{errors} error{'s' * (errors != 1)}, {warns} warning{'s' * (warns != 1)}", errors == 0


def _scale_bar(x: float, y: float, s: float, span_m: float) -> tuple[str, float]:
    step = next(v for v in (1, 2, 5, 10, 20, 50) if v * s >= 40)
    n = max(2, min(5, int(span_m / 4 / step)))
    bars = "".join(f'<rect x="{x + i * step * s:.1f}" y="{y}" width="{step * s:.1f}" height="6" '
                   f'fill="{P["ink"] if i % 2 == 0 else P["paper"]}" stroke="{P["ink"]}" stroke-width="0.8"/>'
                   for i in range(n))
    ticks = "".join(f'<text x="{x + i * step * s:.1f}" y="{y + 20}" class="tick">{i * step}</text>' for i in range(n + 1))
    unit = f'<text x="{x + n * step * s + 8:.1f}" y="{y + 20}" class="tick" text-anchor="start">m</text>'
    return bars + ticks + unit, n * step * s + 20


def _north(x: float, y: float) -> str:
    return (f'<g transform="translate({x} {y})"><circle r="16" fill="none" stroke="{P["ink"]}" stroke-width="1"/>'
            f'<path d="M0 -13 L6 8 L0 4 L-6 8 Z" fill="{P["ink"]}"/>'
            f'<text y="34" class="tick">Plan N</text></g>')


def render(scene: dict) -> str:
    """Return the plan as an SVG document string."""
    rooms = {r["id"]: r for r in scene["rooms"]}
    boxes = [(lv, _bounds(scene, lv["id"])) for lv in scene["levels"]]
    total_w = sum(b[2] - b[0] for _, b in boxes)
    s = min(48.0, 1150 / total_w)  # px per meter
    width = MARGIN * 2 + total_w * s + (GAP + 2 * SIGN_PAD) * len(boxes) - GAP
    height_m = max(b[3] - b[1] for _, b in boxes)
    width = max(width, 900)
    height = MARGIN + 76 + height_m * s + STRIP
    body, x_off = [], MARGIN
    for level, (x0, z0, x1, z1) in boxes:
        tx = lambda p, ox=x_off + SIGN_PAD, bx=x0, bz=z0: ((p[0] - bx) * s + ox, (p[1] - bz) * s + MARGIN + 56)  # noqa: E731
        body.append(f'<text x="{x_off}" y="{MARGIN + 12}" class="level">{escape(level["name"])}</text>')
        body.append(f'<g class="level-plan" data-level={quoteattr(level["id"])}>{_level_svg(scene, level, rooms, tx, s)}</g>')
        x_off += (x1 - x0) * s + GAP + 2 * SIGN_PAD
    meta = scene["meta"]
    status, ok = _status(scene)
    top = height - STRIP + 24
    used_types = sorted({r["type"] for r in scene["rooms"]})
    used_poi = sorted({p["kind"] for p in scene.get("points_of_interest", []) if p["kind"] in POI})
    legend = [(P.get(f"room-{t}", P["room-unknown"]), t.capitalize(), "") for t in used_types]
    legend += [(C["exit"], "Exit", ""), ("none", "Walkable boundary", "dash")]
    legend += [(POI[k][1], POI[k][2], POI[k][0]) for k in used_poi]
    bar, bar_w = _scale_bar(MARGIN, top + 4, s, total_w)
    items = []
    for i, (fill, text, mark) in enumerate(legend):
        lx, ly = MARGIN + bar_w + 48 + (i // 4) * 170, top + 4 + (i % 4) * 22
        swatch = (f'<line x1="{lx}" y1="{ly + 7}" x2="{lx + 22}" y2="{ly + 7}" stroke="{P["walkable"]}" stroke-dasharray="4 3"/>'
                  if mark == "dash" else f'<rect x="{lx}" y="{ly}" width="22" height="14" rx="2" fill="{fill}" stroke="{P["line"]}" stroke-width="0.5"/>')
        items.append(swatch + f'<text x="{lx + 30}" y="{ly + 11}" class="legend">{escape(text)}</text>')
    tb_x = width - MARGIN - 340
    source = Path(meta["source_file"]).name
    source = source if len(source) <= 20 else source[:19] + "..."
    rows = [("Source", f'{meta["source"]} ({source})'),
            ("Scale", f'{meta["scale_method"].replace("_", " ")}, confidence {meta["scale_confidence"]:.2f}'),
            ("Generated", meta["generated_at"][:10]), ("Checks", status)]
    block = [f'<rect x="{tb_x}" y="{top - 8}" width="340" height="{STRIP - 40}" fill="none" stroke="{P["ink"]}" stroke-width="1"/>',
             f'<text x="{tb_x + 14}" y="{top + 18}" class="title">{escape(meta["name"])}</text>']
    for i, (k, v) in enumerate(rows):
        cls = "val ok" if k == "Checks" and ok else "val bad" if k == "Checks" else "val"
        block.append(f'<text x="{tb_x + 14}" y="{top + 42 + i * 17}" class="key">{k}</text>'
                     f'<text x="{tb_x + 92}" y="{top + 42 + i * 17}" class="{cls}">{escape(v)}</text>')
    style = (f"@import url('{FONT_IMPORT}');"
             f"text{{font-family:{F['sans']};fill:{P['ink']}}}"
             f".name{{font:600 13px {F['condensed']};text-anchor:middle}}"
             f".area{{font:400 10px {F['mono']};fill:{P['text-muted']};text-anchor:middle}}"
             f".name,.area{{paint-order:stroke;stroke:{P['paper']};stroke-width:3px;stroke-linejoin:round}}"
             f".sign{{font:600 13px {F['sans']};fill:#FFF}}"
             f".code{{font:500 10px {F['mono']};fill:#FFF;text-anchor:middle}}"
             f".level{{font:600 16px {F['sans']}}}.title{{font:600 20px {F['sans']}}}"
             f".key,.legend{{font-size:12px;fill:{P['text-muted']}}}.legend{{fill:{P['ink']}}}"
             f".val{{font:500 11px {F['mono']}}}.ok{{fill:{C['exit']}}}.bad{{fill:{C['hazard']}}}"
             f".tick{{font:400 10px {F['mono']};text-anchor:middle;fill:{P['text-muted']}}}")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}" font-size="12"><title>{escape(meta["name"])} plan</title>'
            f'<style><![CDATA[{style}]]></style><rect width="100%" height="100%" fill="{P["paper"]}"/>'
            + "".join(body)
            + f'<line x1="{MARGIN}" y1="{height - STRIP}" x2="{width - MARGIN}" y2="{height - STRIP}" stroke="{P["line"]}" stroke-width="0.6"/>'
            + bar + _north(width - MARGIN - 16, MARGIN + 4)
            + "".join(items) + "".join(block) + "</svg>\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scene", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="output .svg (default: plan.svg next to scene.json)")
    a = ap.parse_args(argv)
    out = a.out or a.scene.with_name("plan.svg")
    svg = render(json.loads(a.scene.read_text()))
    out.write_text(svg)
    print(f"wrote {out}")
    try:
        import cairosvg
    except ImportError:
        return 0
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out.with_suffix(".png")), output_width=2400)
    print(f"wrote {out.with_suffix('.png')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
