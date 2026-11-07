"""Local scenario API for the viewer's editing UI: python -m scenario.serve [--port 8792].

POST /parse {scene_path, text, at?: {point: [x, z], level}, t_s?, history?}
  -> {events, occupants, questions, assumptions, cost_usd, click}  (scenario/parse.py)
POST /chat  {scene_path, messages: [{role: user|assistant, content}]}
  -> {scenario?, events, occupants, assumptions, questions, messages, cost_usd}
     A question ends the turn: the client appends the user's answer to messages and posts again.
POST /populate {scene_path, total, mode?, preset?, personas?, levels?, seed?, traits?} -> {occupants, notes}
  (scenario/populate.py; mode llm, alias ai, uses the seeding LLM)
GET /health. CORS for localhost only. scene_path must be a .json file inside the repo.
"""

from __future__ import annotations

import argparse
import json
import re
import traceback
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .chat import NeedUser, raise_need_user, run
from .parse import parse_event
from .populate import populate

REPO = Path(__file__).resolve().parents[1]
MAX_BODY = 200_000
MAX_TEXT = 1000
MAX_TURNS_IN = 40
LOCAL_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")


@lru_cache(maxsize=16)
def _load(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text())


def load_scene(scene_path: str) -> tuple[dict, str]:
    """Scene dict and its repo-relative path; ValueError for anything outside the repo or not a .json file."""
    p = (REPO / scene_path).resolve()
    if p.suffix != ".json" or not p.is_relative_to(REPO) or not p.is_file():
        raise ValueError(f"scene_path must be an existing .json file inside the repo, got {scene_path!r}")
    return _load(str(p), p.stat().st_mtime), str(p.relative_to(REPO))


def _history(messages: object) -> list[dict]:
    if not isinstance(messages, list) or not messages or len(messages) > MAX_TURNS_IN:
        raise ValueError(f"messages must be a list of 1-{MAX_TURNS_IN} {{role, content}} turns")
    out = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            raise ValueError("each message needs role user|assistant and string content")
        out.append({"role": m["role"], "content": m["content"][:MAX_TEXT]})
    if out[-1]["role"] != "user":
        raise ValueError("the last message must be from the user")
    return out


def handle_parse(req: dict) -> dict:
    scene, path = load_scene(str(req.get("scene_path", "")))
    text = str(req.get("text", "")).strip()
    if not text or len(text) > MAX_TEXT:
        raise ValueError(f"text must be 1-{MAX_TEXT} characters")
    at = req.get("at")
    if at is not None and not (isinstance(at, dict) and isinstance(at.get("point"), list) and len(at["point"]) == 2
                               and all(isinstance(v, (int, float)) for v in at["point"])):
        raise ValueError('at must be {"point": [x, z], "level": id} or null')
    history = _history(req["history"]) if req.get("history") else None
    return parse_event(scene, text, at, float(req.get("t_s") or 0.0), scene_path=path, history=history)


def handle_chat(req: dict) -> dict:
    scene, path = load_scene(str(req.get("scene_path", "")))
    history = _history(req.get("messages"))
    out = {"events": [], "occupants": [], "assumptions": [], "questions": [], "cost_usd": 0.0}
    try:
        scenario, cost, _ = run(scene, path, None, raise_need_user, history=history)
    except NeedUser as q:
        return {**out, "questions": [{"question": q.question, "options": q.options}], "cost_usd": round(q.cost, 6),
                "messages": history + [{"role": "assistant", "content": q.question}]}
    if scenario is None:
        reply = "I could not build a valid scenario from that. Can you rephrase it?"
        return {**out, "questions": [{"question": reply, "options": []}],
                "messages": history + [{"role": "assistant", "content": reply}]}
    summary = f"Scenario ready: {len(scenario['events'])} events, {sum(o['count'] for o in scenario['occupants'])} people."
    return {**out, "scenario": scenario, "events": scenario["events"], "occupants": scenario["occupants"],
            "assumptions": scenario["assumptions"], "cost_usd": round(cost, 6),
            "messages": history + [{"role": "assistant", "content": summary}]}


def handle_populate(req: dict) -> dict:
    scene, _ = load_scene(str(req.get("scene_path", "")))
    total, levels = req.get("total"), req.get("levels")
    if levels is not None and not (isinstance(levels, list) and all(isinstance(x, str) for x in levels)):
        raise ValueError("levels must be a list of level ids")
    personas = req.get("personas")
    if personas is not None and not isinstance(personas, dict):
        raise ValueError("personas must be an object of weights")
    traits = req.get("traits")
    if traits is not None and not isinstance(traits, dict):
        raise ValueError("traits must be an object")
    occupants, notes = populate(scene, total, str(req.get("mode", "load")), str(req.get("preset", "office_day")),
                                personas, levels or None, int(req.get("seed", 42)), traits=traits)
    return {"occupants": occupants, "notes": notes}


ROUTES = {"/parse": handle_parse, "/chat": handle_chat, "/populate": handle_populate}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        origin = self.headers.get("Origin") or ""
        if LOCAL_ORIGIN.match(origin):
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
            self._send(200, {"ok": True, "routes": sorted(ROUTES)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        route = ROUTES.get(self.path.rstrip("/"))
        if route is None:
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": f"body over {MAX_BODY} bytes"})
            req = json.loads(self.rfile.read(n))
            if not isinstance(req, dict):
                raise ValueError("body must be a JSON object")
            self._send(200, route(req))
        except ValueError as e:  # bad input, including JSON decode errors
            self._send(400, {"error": str(e)[:300]})
        except Exception as e:  # log detail server side, short message to the client
            traceback.print_exc()
            self._send(500, {"error": f"{self.path} failed: {type(e).__name__}"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"scenario API on http://{a.host}:{a.port} (POST /parse, POST /chat)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
