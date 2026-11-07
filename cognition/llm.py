"""OpenRouter chat call with a sha256-keyed disk cache (same style as ingest_lidar/semantics.py).

Tests replace `chat` with a fake; nothing else in cognition/ touches the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import certifi

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "data" / "llm_cache" / "cognition"
URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"
_CTX = ssl.create_default_context(cafile=certifi.where())
_LOCK = threading.Lock()
STATS = {"calls": 0, "cached": 0, "cost_usd": 0.0, "latency_ms": [], "errors": 0}
_BILLING_STOPPED = False


def _stop_for_billing() -> None:
    """One 402 means the wallet is empty. Later calls in this process must not keep hitting the API."""
    global _BILLING_STOPPED
    _BILLING_STOPPED = True


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


def available() -> bool:
    return _key() is not None


JSON_ONLY = "\n\nReply with the JSON object only: no prose before or after it, no code fences."
# Per-model request shapes, first matching prefix wins (research/MODEL_CASTING_RUN.md):
# gpt-oss rejects reasoning off ("Reasoning is mandatory") and spends hidden tokens, so low effort and room;
# llama-4 has no endpoint that accepts response_format here, so ask for JSON in the prompt instead.
SHAPES: list[tuple[str, dict]] = [
    ("openai/gpt-oss", {"reasoning": {"effort": "low"}, "min_tokens": 400}),
    ("meta-llama/", {"reasoning": None, "response_format": None, "json_hint": True}),
]


def shape(model: str) -> dict:
    return next((s for prefix, s in SHAPES if model.startswith(prefix)), {})


def _body(messages: list[dict], model: str, temperature: float, max_tokens: int) -> dict:
    """Request body; models without an entry in SHAPES get exactly the default body (cache keys unchanged)."""
    sh = shape(model)
    if sh.get("json_hint") and messages and messages[-1].get("role") == "user":
        messages = messages[:-1] + [{**messages[-1], "content": messages[-1]["content"] + JSON_ONLY}]
    body = {"model": model, "messages": messages, "temperature": temperature,
            "max_tokens": max(max_tokens, sh.get("min_tokens", 0)),
            "reasoning": sh.get("reasoning", {"enabled": False}),
            "response_format": sh.get("response_format", {"type": "json_object"}), "usage": {"include": True}}
    return {k: v for k, v in body.items() if v is not None}


def _hit(body: dict, tag: str) -> Path:
    return CACHE / f"{hashlib.sha256((json.dumps(body, sort_keys=True) + chr(0) + tag).encode()).hexdigest()}.json"


def _cached(hit: Path) -> tuple[str, dict] | None:
    if not hit.exists():
        return None
    with _LOCK:
        STATS["cached"] += 1
    return json.loads(hit.read_text())["content"], {"cached": True, "latency_ms": 0, "cost_usd": 0.0}


def _store(hit: Path, model: str, resp: dict, ms: int) -> tuple[str, dict]:
    content = resp["choices"][0]["message"]["content"] or ""
    cost = float((resp.get("usage") or {}).get("cost") or 0.0)
    with _LOCK:
        STATS["calls"] += 1
        STATS["cost_usd"] += cost
        STATS["latency_ms"].append(ms)
    CACHE.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps({"model": model, "content": content, "cost_usd": cost}))
    return content, {"cached": False, "latency_ms": ms, "cost_usd": cost}


def chat(messages: list[dict], model: str = DEFAULT_MODEL, temperature: float = 0.7, max_tokens: int = 260,
         tag: str = "", timeout: float = 8.0) -> tuple[str, dict]:
    """Return (content, meta). `tag` separates independent samples of the same prompt in the cache.

    meta = {cached, latency_ms, cost_usd}. Raises on HTTP, timeout or missing key; callers fall back.
    """
    body = _body(messages, model, temperature, max_tokens)
    hit = _hit(body, tag)
    if (c := _cached(hit)) is not None:
        return c
    key = _key()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    if _BILLING_STOPPED:
        raise RuntimeError("OpenRouter returned 402; model calls are paused until the server restarts")
    req = urllib.request.Request(URL, json.dumps(body).encode(),
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as exc:
        if exc.code == 402:
            _stop_for_billing()
        with _LOCK:
            STATS["errors"] += 1
        raise
    except Exception:
        with _LOCK:
            STATS["errors"] += 1
        raise
    return _store(hit, model, resp, int((time.perf_counter() - t0) * 1000))


class RateLimited(RuntimeError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"429, retry after {retry_after:.1f} s")
        self.retry_after = retry_after


async def achat(client, messages: list[dict], model: str = DEFAULT_MODEL, temperature: float = 0.7,
                max_tokens: int = 260, tag: str = "", timeout: float = 8.0) -> tuple[str, dict]:
    """Async twin of chat() on a shared httpx.AsyncClient (connection reuse). Raises RateLimited on 429."""
    body = _body(messages, model, temperature, max_tokens)
    hit = _hit(body, tag)
    if (c := _cached(hit)) is not None:
        return c
    key = _key()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    if _BILLING_STOPPED:
        raise RuntimeError("OpenRouter returned 402; model calls are paused until the server restarts")
    t0 = time.perf_counter()
    try:
        r = await client.post(URL, json=body, timeout=timeout, headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 402:
            _stop_for_billing()
            raise RuntimeError("402 Payment Required")
        if r.status_code == 429:
            raise RateLimited(float(r.headers.get("retry-after") or 1.0))
        r.raise_for_status()
        resp = r.json()
    except Exception:
        with _LOCK:
            STATS["errors"] += 1
        raise
    return _store(hit, model, resp, int((time.perf_counter() - t0) * 1000))


def parse_json(text: str) -> dict:
    """First JSON object in the text; tolerates code fences, a stray sentence or trailing junk after it."""
    return json.JSONDecoder().raw_decode(text[text.find("{"):])[0]
