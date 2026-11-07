"""Local interview API for the viewer: python -m cognition.serve --runs out/sim [--port 8770].

POST /interview {run, agent_id, question} -> InterviewAnswer JSON. GET /health. CORS for localhost.
"""

from __future__ import annotations

import argparse
import json
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .interview import interview

ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}$")  # no leading dot: blocks ".." traversal
MAX_Q = 500


def resolve_run(root: Path, run: str) -> Path | None:
    """A run is a subdirectory name or the meta.run_id in its replay.json (what the viewer sends)."""
    if (root / run / "decisions.jsonl").exists():
        return root / run
    for f in sorted(root.glob("*/replay.json")):
        try:
            if json.loads(f.read_text()).get("meta", {}).get("run_id") == run:
                return f.parent
        except (OSError, ValueError):
            continue
    return None


class Handler(BaseHTTPRequestHandler):
    runs: Path = Path("out/sim")

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        origin = self.headers.get("Origin") or ""
        if re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, {})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            runs = sorted(p.parent.name for p in self.runs.glob("*/decisions.jsonl"))
            self._send(200, {"ok": True, "runs": runs})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/interview":
            return self._send(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 10_000)))
            run, agent, q = str(req.get("run", "")), str(req.get("agent_id", "")), str(req.get("question", "")).strip()
        except (ValueError, TypeError):
            return self._send(400, {"error": "body must be JSON {run, agent_id, question}"})
        if not ID_RE.match(run) or not ID_RE.match(agent) or not q or len(q) > MAX_Q:
            return self._send(400, {"error": f"need run and agent_id ids and a question of 1-{MAX_Q} chars"})
        run_dir = resolve_run(self.runs, run)
        if run_dir is None:
            return self._send(404, {"error": f"run {run} not found under {self.runs}"})
        try:
            self._send(200, interview(run_dir, agent, q))
        except (FileNotFoundError, KeyError) as e:
            self._send(404, {"error": str(e).strip("'\"")})
        except Exception as e:  # log full detail server side, short message to the client
            traceback.print_exc()
            self._send(500, {"error": f"interview failed: {type(e).__name__}"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", default="out/sim", help="directory whose subdirs hold decisions.jsonl")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    Handler.runs = Path(a.runs)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"interview API on http://{a.host}:{a.port}/interview (runs: {a.runs})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
