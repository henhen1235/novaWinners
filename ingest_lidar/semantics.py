"""LLM semantics (room type and name) via OpenRouter. Never touches coordinates.

Env: OPENROUTER_API_KEY (or the repo .env), OPENROUTER_MODEL (default below).
Every call is cached on disk under data/llm_cache/ keyed by SHA-256 of
(model, prompt, image bytes), so reruns are free and deterministic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import ssl
import urllib.request
from pathlib import Path

import certifi
from shapely.geometry import Polygon

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "data" / "llm_cache"
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
ROOM_TYPES = ["office", "corridor", "stairwell", "lobby", "restroom", "classroom", "storage", "mechanical",
              "outside", "unknown"]


def _key() -> str | None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "OPENROUTER_API_KEY":
                return v.strip().strip("'\"")
    return None


def complete(prompt: str, image: bytes = b"", model: str | None = None, max_tokens: int = 800) -> str:
    """One cached chat completion returning the message text. Raises on HTTP/key errors."""
    model = model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    digest = hashlib.sha256(model.encode() + b"\0" + prompt.encode() + b"\0" + image).hexdigest()
    hit = CACHE / f"{digest}.json"
    if hit.exists():
        return json.loads(hit.read_text())["content"]
    key = _key()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    content: list | str = prompt
    if image:
        url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
        content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]
    system = "Reply with one JSON object only. No prose, no markdown fences."
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": max_tokens, "reasoning": {"effort": "none"}, "response_format": {"type": "json_object"}}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", json.dumps(body).encode(),
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    text = text[text.find("{"): text.rfind("}") + 1]  # tolerate fences or a stray sentence
    json.loads(text)  # only cache parseable answers
    CACHE.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps({"model": model, "content": text}))
    return text


def _describe(scene: dict) -> list[dict]:
    """Geometry-only room descriptors for the prompt."""
    out = []
    for r in scene["rooms"]:
        poly = Polygon(r["polygon"])
        xs, zs = poly.minimum_rotated_rectangle.exterior.coords.xy
        sides = sorted(round(((xs[i + 1] - xs[i]) ** 2 + (zs[i + 1] - zs[i]) ** 2) ** 0.5, 1) for i in range(2))
        doors = [d for d in scene["doors"] if r["id"] in d["connects"]]
        obs = [o for o in scene["obstacles"] if poly.contains(Polygon(o["polygon"]).centroid)]
        out.append({"id": r["id"], "area_m2": round(poly.area, 1), "size_m": sides, "doors": len(doors),
                    "has_exit": any(d["is_exit"] for d in doors),
                    "furniture": [{"kind": o["kind"], "h": o["height_m"]} for o in obs][:12]})
    return out


def label_rooms(scene: dict) -> dict:
    """Return a copy of scene with LLM-proposed room types/names; on failure add a warning."""
    prompt = ("You label rooms in a scanned building floor for an evacuation simulation. For each room "
              f"choose type from {ROOM_TYPES} and a short human name (max 30 chars, e.g. 'Main corridor', "
              "'Office 2', 'Storage closet'). Use only the descriptors given. Return JSON "
              '{"rooms": [{"id": "...", "type": "...", "name": "..."}]}.\nRooms: ' + json.dumps(_describe(scene)))
    try:
        got = {x["id"]: x for x in json.loads(complete(prompt))["rooms"]}
    except Exception as e:  # degrade gracefully: geometry stays valid without names
        warn = {"code": "LLM_UNAVAILABLE", "message": f"room labels not generated: {type(e).__name__}: {e}"[:200],
                "ref": None, "severity": "warn"}
        return {**scene, "warnings": scene["warnings"] + [warn]}
    rooms = []
    for r in scene["rooms"]:
        g = got.get(r["id"], {})
        rooms.append({**r, "type": g.get("type") if g.get("type") in ROOM_TYPES else r["type"],
                      "name": str(g.get("name") or r["name"])[:40]})
    ext = {**scene["extensions"], "semantics": {"model": os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
                                               "source": "llm", "fields": ["rooms.type", "rooms.name"]}}
    return {**scene, "rooms": rooms, "extensions": ext}
