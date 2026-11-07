"""python -m loop report out/loop/<name> -> report.html: one self-contained page (design/tokens.css inlined),
scoreboard table, inline SVG charts per metric (median bar, seed range whisker), the adversary's and the
optimizer's reasoning, and viewer links for each replay (?replay=...&compare=...)."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from .run_loop import fmt

REPO = Path(__file__).resolve().parents[1]
TOKENS = REPO / "design" / "tokens.css"
CHARTS = [("t95_s", "Time to clear 95% (s)"), ("t90_s", "Time to clear 90% (s)"), ("stragglers", "Stragglers"), ("misled_by_rumor", "Misled by rumor"),
          ("max_queue", "Max queued at once")]
COLOR = {"baseline": "var(--color-line-strong)", "attack": "var(--color-hazard-bright)", "fixed": "var(--color-exit-bright)"}
VIEWER = "http://localhost:5173/"
E = html.escape


def _kind(stage: str) -> str:
    return "baseline" if stage.endswith("baseline") else "attack" if stage.endswith("attack") else "fixed"


def _val(s: dict, k: str) -> float:
    v = s.get(k)
    return float(s["duration_s"] if k in ("t95_s", "t90_s") and v is None else (v or 0))


def chart(board: dict, key: str, title: str) -> str:
    """Horizontal bars: median per stage, whisker = min..max over seeds, dots = each seed."""
    stages = board["stages"]
    vals = [[_val(s, key) for s in st["per_seed"].values()] for st in stages]
    top = max([max(v) for v in vals] + [1.0])
    w, label_w, row, pad = 520, 150, 26, 8
    h = row * len(stages) + 2 * pad + 18
    x = lambda v: label_w + (w - label_w - 40) * v / top  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{E(title)}" class="chart">',
           f'<text x="0" y="12" class="ct">{E(title)}</text>']
    for i, (st, v) in enumerate(zip(stages, vals)):
        y = 18 + pad + i * row
        med = st["median"][key]
        col = COLOR[_kind(st["stage"])]
        out.append(f'<text x="0" y="{y + 14}" class="cl">{E(st["label"])}</text>')
        out.append(f'<rect x="{label_w}" y="{y + 4}" width="{max(x(med) - label_w, 1):.1f}" height="{row - 10}" fill="{col}" rx="2"/>')
        out.append(f'<line x1="{x(min(v)):.1f}" x2="{x(max(v)):.1f}" y1="{y + row / 2 - 1}" y2="{y + row / 2 - 1}" class="wh"/>')
        out += [f'<circle cx="{x(s):.1f}" cy="{y + row / 2 - 1}" r="2.5" class="dot"/>' for s in v]
        out.append(f'<text x="{x(max(v)) + 6:.1f}" y="{y + 14}" class="cv">{med:g}</text>')
    out.append("</svg>")
    return "".join(out)


def _viewer_links(board: dict, d: Path) -> str:
    """Links that compare each stage's first seed with the stage before it, in the split viewer."""
    name = d.name
    base = f"/loop/{name}"
    glb = f"&glb={base}/display.glb" if (d / "display.glb").exists() else ""
    seed = board["seeds"][0]
    rows, prev = [], None
    for st in board["stages"]:
        scene = f"{base}/{st['stage']}/scene.json" if st["scene"] != board["scene"] else f"{base}/scene.json"
        rep = f"{base}/{st['stage']}/s{seed}/replay.json"
        url = f"{VIEWER}?scene={scene}{glb}&replay={rep}" + (f"&compare={base}/{prev}/s{seed}/replay.json" if prev else "")
        rows.append(f'<li><a href="{E(url)}">{E(st["label"])}</a>' + (f" vs {E(prev)}" if prev else "") + "</li>")
        prev = st["stage"]
    cmd = (f"mkdir -p blueprint/viewer/public/loop && ln -sfn \"$PWD/{d}\" blueprint/viewer/public/loop/{name}\n"
           f"(cd blueprint/viewer && npx vite)   # interviews: .venv/bin/python -m cognition.serve")
    return f"<pre>{E(cmd)}</pre><ul>{''.join(rows)}</ul>"


def _table(board: dict) -> str:
    head = "<tr><th>stage</th><th>t95 s</th><th>t90 s</th><th>evacuated</th><th>stragglers</th><th>misled</th><th>max queue</th><th>LLM calls</th><th>cost $</th></tr>"
    body = []
    for st in board["stages"]:
        m, ps = st["median"], list(st["per_seed"].values())
        rng = lambda k: f'<span class="rg">{min(_val(s, k) for s in ps):g}-{max(_val(s, k) for s in ps):g}</span>'  # noqa: E731
        body.append(f'<tr class="{_kind(st["stage"])}"><td>{E(st["label"])}</td><td>{E(fmt(m, "t95_s"))} {rng("t95_s")}</td><td>{m["t90_s"]:g} {rng("t90_s")}</td>'
                    f'<td>{m["evacuated"]:g}/{m["total"]}</td><td>{m["stragglers"]:g} {rng("stragglers")}</td>'
                    f'<td>{m["misled_by_rumor"]:g} {rng("misled_by_rumor")}</td><td>{m["max_queue"]:g}</td>'
                    f'<td>{m["llm_calls"]:g}</td><td>{m["cost_usd"]:.4f}</td></tr>')
    return f"<table>{head}{''.join(body)}</table>"


def _rounds(board: dict) -> str:
    out = []
    for r in board["rounds"]:
        adv = "".join(f'<li><code>{E(i["event"]["type"])}</code> {E(json.dumps(i["event"]["at"]))} at '
                      f'{i["event"]["t_start_s"]:g} s<p>{E(i["why"])}</p></li>' for i in r["adversary"]["injections"])
        opt = "".join(f'<li><code>{E(iv["kind"])}</code> {E(json.dumps(iv["args"]))}<p>{E(iv["why"])}</p></li>'
                      for iv in r["optimizer"]["interventions"])
        imp = "".join(f'<li>{E(k)}: <b>{d["median"]:+g}</b> <span class="rg">[{d["min"]:+g}, {d["max"]:+g}]</span></li>'
                      for k, d in r["improvement"].items())
        out.append(f'<section><h2>Round {r["round"]}</h2><div class="cols">'
                   f'<div class="card atk"><h3>Adversary <small>{E(r["adversary"]["source"])}</small></h3><ol>{adv}</ol></div>'
                   f'<div class="card fix"><h3>Optimizer <small>{E(r["optimizer"]["source"])}</small></h3><ol>{opt}</ol></div>'
                   f'</div><p class="note">Attacked to fixed, paired per seed, median [min, max]; negative is better:</p>'
                   f'<ul class="imp">{imp}</ul></section>')
    return "".join(out)


CSS = """
body{margin:0;background:var(--color-ground);color:var(--color-text);font-family:var(--font-sans);font-size:var(--size-md)}
main{max-width:1040px;margin:0 auto;padding:var(--space-6) var(--space-4)}
h1{font-family:var(--font-condensed);font-size:var(--size-3xl);margin:0 0 var(--space-2)}
h2{font-family:var(--font-condensed);font-size:var(--size-2xl);margin:var(--space-6) 0 var(--space-3)}
h3{margin:0 0 var(--space-2);font-size:var(--size-lg)} h3 small{color:var(--color-text-muted);font-weight:400}
.meta,.note,.rg{color:var(--color-text-muted)} .rg{font-size:var(--size-xs);font-family:var(--font-mono)}
table{border-collapse:collapse;width:100%;font-family:var(--font-mono);font-size:var(--size-sm)}
th,td{text-align:left;padding:var(--space-2);border-bottom:1px solid var(--color-line)}
tr.attack td:first-child{border-left:3px solid var(--color-hazard-bright)} tr.fixed td:first-child{border-left:3px solid var(--color-exit-bright)}
tr.baseline td:first-child{border-left:3px solid var(--color-line-strong)}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:var(--space-4)}
.chart{width:100%;background:var(--color-surface);border-radius:var(--radius-sm);padding:var(--space-2);box-sizing:border-box}
.ct{fill:var(--color-text);font-size:13px;font-weight:600} .cl,.cv{fill:var(--color-text-muted);font-size:11px;font-family:var(--font-mono)}
.wh{stroke:var(--color-text);stroke-width:1.5} .dot{fill:var(--color-text)}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:var(--space-4)}
.card{background:var(--color-surface);padding:var(--space-4);border-radius:var(--radius-sm);border-top:3px solid var(--color-line)}
.card.atk{border-top-color:var(--color-hazard-bright)} .card.fix{border-top-color:var(--color-exit-bright)}
.card p{margin:var(--space-1) 0 var(--space-3);color:var(--color-text-muted);font-size:var(--size-sm)}
code,pre{font-family:var(--font-mono);font-size:var(--size-sm)} pre{background:var(--color-surface);padding:var(--space-3);overflow-x:auto}
a{color:var(--color-drafting)} .cost{font-family:var(--font-mono);font-size:var(--size-sm)}
"""


def render(board: dict, d: Path) -> str:
    c = board["cost"]
    tokens = TOKENS.read_text() if TOKENS.exists() else ""
    cost = (f'Occupant decisions ${c["occupant_decisions_usd"]} ({c["occupant_llm_calls"]} LLM calls), adversary + optimizer '
            f'${c["adversary_optimizer_usd"]} ({c["adversary_optimizer_requests"]} requests), total ${c["total_usd"]}; '
            f'median per-tick LLM latency {board["latency_per_tick_ms_p50_median"]} ms')
    charts = "".join(chart(board, k, t) for k, t in CHARTS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Loop scoreboard {E(board["name"])}</title>
<style>{tokens}{CSS}</style></head><body><main>
<h1>Adversary vs optimizer: {E(board["name"])}</h1>
<p class="meta">Scene <code>{E(board["scene"])}</code>, {board["occupants"]} occupants, decisions by {E(board["decide"])},
seeds {E(", ".join(map(str, board["seeds"])))}. Medians over seeds; t95 counts the original occupants only.</p>
<p class="cost">{E(cost)}</p>
<h2>Scoreboard</h2>{_table(board)}
<h2>Per stage</h2><div class="charts">{charts}</div>
{_rounds(board)}
<h2>Open the replays</h2>{_viewer_links(board, d)}
</main></body></html>"""


def write_report(d: Path) -> Path:
    board = json.loads((d / "scoreboard.json").read_text())
    scene = REPO / board["scene"] if not Path(board["scene"]).is_absolute() else Path(board["scene"])
    if scene.exists() and not (d / "scene.json").exists():
        shutil.copy(scene, d / "scene.json")
    glb = scene.with_name("display.glb")
    if glb.exists() and not (d / "display.glb").exists():
        (d / "display.glb").symlink_to(glb.resolve())
    out = d / "report.html"
    out.write_text(render(board, d))
    return out
