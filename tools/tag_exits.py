"""Click-to-tag exits on a pipeline run's top-down overview.

Usage: python -m tools.tag_exits out/<run> [--port 8765]
Opens a local page showing debug/overview.png; click a doorway, type a label,
then Save. Writes exit_tags.json ([[x, z, label], ...] in scene meters) to the
run dir. Rerun the pipeline with --exit-tags out/<run>/exit_tags.json; the
pipeline is deterministic, so the scene frame does not move between runs.
Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TOKENS_CSS = (Path(__file__).resolve().parents[1] / "design" / "tokens.css").read_text()
PAGE = """<!doctype html><meta charset=utf-8><title>Tag exits</title>
<style>%TOKENS%
body{margin:0;background:var(--color-ground);color:var(--color-text);font:var(--size-sm)/1.45 var(--font-sans)}
header{display:flex;align-items:center;gap:var(--space-4);padding:var(--space-3) var(--space-4);
background:var(--color-surface);border-bottom:1px solid var(--color-line)}
h1{margin:0;font:600 var(--size-xl)/1 var(--font-sans)}
header p{flex:1;margin:0;color:var(--color-text-muted)}
button{font:600 var(--size-sm) var(--font-sans);background:var(--color-exit);color:var(--color-on-signal);
border:0;border-radius:var(--radius-sm);padding:8px 14px;cursor:pointer}
button:focus-visible,a:focus-visible{outline:none;box-shadow:var(--shadow-focus)}
#s{font:var(--size-xs) var(--font-mono);color:var(--color-exit-bright)}
main{display:flex;gap:var(--space-4);padding:var(--space-4);align-items:flex-start}
ol{margin:0;padding:0 0 0 var(--space-5);min-width:240px}
li{padding:var(--space-2) 0;border-bottom:1px solid var(--color-line)}
li span{font:var(--size-xs) var(--font-mono);color:var(--color-text-muted)}
li a{color:var(--color-hazard-bright);margin-left:var(--space-2)}
img{cursor:crosshair;border:1px solid var(--color-line-strong);max-width:none}</style>
<header><h1>Tag exits</h1><p>Click a doorway to add an exit, name it, then save.
Red marks are exits the pipeline found; blue lines are interior doors.</p>
<span id=s></span><button onclick=save()>Save exit_tags.json</button></header>
<main><img id=i src="/overview.png"><ol id=l></ol></main>
<script>
const G=%GRID%; let tags=%TAGS%;
const draw=()=>{l.innerHTML=tags.map((t,k)=>`<li>${t[2]} <span>(${t[0]}, ${t[1]}) m</span> <a href=# onclick="tags.splice(${k},1);draw()">Remove</a>`).join('')};
i.onclick=e=>{const r=i.getBoundingClientRect();const x=+(G.x0+(e.clientX-r.left)*G.res).toFixed(2),
z=+(G.z0+(e.clientY-r.top)*G.res).toFixed(2);const lab=prompt('Exit label',`Exit ${tags.length+1}`);
if(lab){tags.push([x,z,lab]);draw()}};
const save=()=>fetch('/save',{method:'POST',body:JSON.stringify(tags)}).then(r=>r.text()).then(t=>s.textContent=t);
draw();
</script>"""


def _valid(data: object) -> bool:
    return isinstance(data, list) and all(
        isinstance(t, list) and len(t) == 3 and all(isinstance(v, (int, float)) for v in t[:2]) for t in data)


def serve(run_dir: Path, port: int) -> None:
    """Serve the tagging page on localhost until Ctrl+C."""
    scene = json.loads((run_dir / "scene.json").read_text())
    grid = scene["extensions"]["lidar"]["grid"]
    tag_file = run_dir / "exit_tags.json"
    tags = json.loads(tag_file.read_text()) if tag_file.exists() else []
    page = PAGE.replace("%TOKENS%", TOKENS_CSS).replace("%GRID%", json.dumps(grid)).replace("%TAGS%", json.dumps(tags))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib API)
            png = self.path == "/overview.png"
            body = (run_dir / grid["png"]).read_bytes() if png else page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if png else "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ok = _valid(data)
            if ok:
                tag_file.write_text(json.dumps(data, indent=1))
            self.send_response(200 if ok else 400)
            self.end_headers()
            self.wfile.write(f"saved {len(data)} tags to {tag_file}".encode() if ok else b"bad tags")

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    print(f"tagging at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    webbrowser.open(f"http://127.0.0.1:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    serve(a.run_dir, a.port)
