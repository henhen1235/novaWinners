"""Fix up a pipeline run by hand: tag exits and trace glass walls on the top-down map.

Usage: python -m tools.tag_exits out/<run> [--port 8765]
Opens a local page showing debug/overview.png.
- Exit mode: click a doorway, name it.
- Glass wall mode: click both ends of a wall the scan could not see (glass, open
  shelving). LiDAR sees through glass, so these come out as open floor otherwise.
Save writes corrections.json ({"exit_tags": [[x, z, label]], "walls": [[x1, z1, x2, z2]]},
scene meters) and exit_tags.json to the run dir. Rerun the pipeline with
--corrections out/<run>/corrections.json; the pipeline is deterministic, so the scene
frame does not move between runs. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TOKENS_CSS = (Path(__file__).resolve().parents[1] / "design" / "tokens.css").read_text()
PAGE = """<!doctype html><meta charset=utf-8><title>Fix up scan</title>
<style>%TOKENS%
body{margin:0;background:var(--color-ground);color:var(--color-text);font:var(--size-sm)/1.45 var(--font-sans)}
header{display:flex;align-items:center;gap:var(--space-4);padding:var(--space-3) var(--space-4);
background:var(--color-surface);border-bottom:1px solid var(--color-line)}
h1{margin:0;font:600 var(--size-xl)/1 var(--font-sans)}
header p{flex:1;margin:0;color:var(--color-text-muted)}
button{font:600 var(--size-sm) var(--font-sans);border:0;border-radius:var(--radius-sm);padding:8px 14px;cursor:pointer;
background:var(--color-surface-raised,#24384f);color:var(--color-text)}
button.on{background:var(--color-exit);color:var(--color-on-signal)}
button.save{background:var(--color-exit);color:var(--color-on-signal)}
button:focus-visible,a:focus-visible{outline:none;box-shadow:var(--shadow-focus)}
#s{font:var(--size-xs) var(--font-mono);color:var(--color-exit-bright)}
main{display:flex;gap:var(--space-4);padding:var(--space-4);align-items:flex-start}
ol{margin:0;padding:0 0 0 var(--space-5);min-width:260px}
li{padding:var(--space-2) 0;border-bottom:1px solid var(--color-line)}
li span{font:var(--size-xs) var(--font-mono);color:var(--color-text-muted)}
li a{color:var(--color-hazard-bright);margin-left:var(--space-2)}
#wrap{position:relative;line-height:0}
img,canvas{width:var(--w);height:auto}canvas{position:absolute;left:0;top:0;cursor:crosshair}
img{border:1px solid var(--color-line-strong)}</style>
<header><h1>Fix up scan</h1>
<p>Exit: click a doorway. Glass wall: click both ends of a wall the scanner saw through.</p>
<button id=mExit class=on onclick="mode='exit';ui()">Exit</button>
<button id=mWall onclick="mode='wall';ui()">Glass wall</button>
<span id=s></span><button class=save onclick=save()>Save</button></header>
<main><div id=wrap><img id=i src="/overview.png"><canvas id=c></canvas></div><ol id=l></ol></main>
<script>
const G=%GRID%; let tags=%TAGS%, walls=%WALLS%, mode='exit', pend=null;
const S=2; // display scale: 1 image px = 1 grid cell
const init=()=>{document.body.style.setProperty('--w',(i.naturalWidth*S)+'px');c.width=i.naturalWidth*S;c.height=i.naturalHeight*S;ui()};
// the image can finish loading before this script runs (cache), so check as well as listen
if(i.complete&&i.naturalWidth)init();else i.addEventListener('load',init);
const toPx=(x,z)=>[(x-G.x0)/G.res*S,(z-G.z0)/G.res*S];
const toW=e=>{const r=c.getBoundingClientRect();const k=c.width/r.width;
 return [+(G.x0+(e.clientX-r.left)*k/S*G.res).toFixed(2),+(G.z0+(e.clientY-r.top)*k/S*G.res).toFixed(2)]};
function ui(){mExit.className=mode=='exit'?'on':'';mWall.className=mode=='wall'?'on':'';
 const g=c.getContext('2d');g.clearRect(0,0,c.width,c.height);g.lineCap='round';
 g.strokeStyle='#ff9f1c';g.lineWidth=5;for(const w of walls){const a=toPx(w[0],w[1]),b=toPx(w[2],w[3]);
  g.beginPath();g.moveTo(...a);g.lineTo(...b);g.stroke()}
 if(pend){const a=toPx(...pend);g.fillStyle='#ff9f1c';g.beginPath();g.arc(...a,5,0,7);g.fill()}
 g.fillStyle='#3ddb8e';for(const t of tags){const a=toPx(t[0],t[1]);g.beginPath();g.arc(...a,7,0,7);g.fill()}
 l.innerHTML=tags.map((t,k)=>`<li>Exit: ${t[2]} <span>(${t[0]}, ${t[1]}) m</span> <a href=# onclick="tags.splice(${k},1);ui()">Remove</a>`).join('')
  +walls.map((w,k)=>`<li>Glass wall <span>(${w[0]}, ${w[1]}) to (${w[2]}, ${w[3]})</span> <a href=# onclick="walls.splice(${k},1);ui()">Remove</a>`).join('')}
c.onclick=e=>{const p=toW(e);
 if(mode=='exit'){const lab=prompt('Exit label',`Exit ${tags.length+1}`);if(lab)tags.push([p[0],p[1],lab])}
 else if(!pend){pend=p}else{walls.push([pend[0],pend[1],p[0],p[1]]);pend=null}
 ui()};
const save=()=>fetch('/save',{method:'POST',body:JSON.stringify({exit_tags:tags,walls})}).then(r=>r.text()).then(t=>s.textContent=t);
</script>"""


def _valid(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    tags, walls = data.get("exit_tags", []), data.get("walls", [])
    num = (int, float)
    return (isinstance(tags, list) and isinstance(walls, list)
            and all(isinstance(t, list) and len(t) == 3 and all(isinstance(v, num) for v in t[:2]) for t in tags)
            and all(isinstance(w, list) and len(w) == 4 and all(isinstance(v, num) for v in w) for w in walls))


def serve(run_dir: Path, port: int) -> None:
    """Serve the fix-up page on localhost until Ctrl+C."""
    scene = json.loads((run_dir / "scene.json").read_text())
    grid = scene["extensions"]["lidar"]["grid"]
    corr_file, tag_file = run_dir / "corrections.json", run_dir / "exit_tags.json"
    corr = json.loads(corr_file.read_text()) if corr_file.exists() else {}
    tags = corr.get("exit_tags") or (json.loads(tag_file.read_text()) if tag_file.exists() else [])
    page = (PAGE.replace("%TOKENS%", TOKENS_CSS).replace("%GRID%", json.dumps(grid))
            .replace("%TAGS%", json.dumps(tags)).replace("%WALLS%", json.dumps(corr.get("walls", []))))

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
                corr_file.write_text(json.dumps({**corr, **data}, indent=1))
                tag_file.write_text(json.dumps(data["exit_tags"], indent=1))
            self.send_response(200 if ok else 400)
            self.end_headers()
            msg = (f"saved {len(data['exit_tags'])} exits, {len(data['walls'])} walls to {corr_file}" if ok
                   else "bad data")
            self.wfile.write(msg.encode())

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    print(f"fix-up page at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    webbrowser.open(f"http://127.0.0.1:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    serve(a.run_dir, a.port)
