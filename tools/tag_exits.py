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

PAGE = """<!doctype html><meta charset=utf-8><title>Tag exits</title>
<style>body{font:14px sans-serif;margin:12px}img{cursor:crosshair;border:1px solid #888;max-width:none}</style>
<p>Click a doorway to add an exit tag. Red: auto-detected exits. Blue lines: interior doors.
<button onclick=save()>Save exit_tags.json</button> <span id=s></span></p>
<ol id=l></ol><img id=i src="/overview.png">
<script>
const G=%GRID%; let tags=%TAGS%;
const draw=()=>{l.innerHTML=tags.map((t,k)=>`<li>${t[2]} at (${t[0]}, ${t[1]}) <a href=# onclick="tags.splice(${k},1);draw()">remove</a>`).join('')};
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
    page = PAGE.replace("%GRID%", json.dumps(grid)).replace("%TAGS%", json.dumps(tags))

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
