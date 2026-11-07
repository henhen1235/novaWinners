"""Non-blocking decisions for the sim's live loop (docs/LIVE_MODE.md sections 2 and 3).

    live = Live()
    handles = live.submit(views)     # returns at once; LLM calls run on a background event loop
    for r in live.poll(): ...        # {"handle", "agent_id", "tick", "choice", "say", "record"} once ready
    live.expire(handle)              # past the sim's deadline: persona fallback, late answer dropped

Inside one submit: identical (persona, cues, options) states share one call; more than BATCH_OVER
unique states go out 8-10 per call with an array answer, each entry validated against its own
agent's evidence ids; a view with "prefetch": true is computed now and reused by a later submit
whose state matches. Records and per-agent memory (MINDS) are committed in poll(), on the
caller's thread, exactly as decide_batch() does; offline and replay modes answer immediately.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import math
import queue
import threading
import time

import httpx

from . import llm
from .decide import (CONFIG, REPLAY, _ENV_DONE, _record, _remember, _result, build_inputs, compute_state, configure,
                     fallback, model_for, prompt_for, validate, validate_ctx)
from .prompt import SYSTEM

BATCH_OVER, BATCH_MAX = 8, 10
MAX_IN_FLIGHT, MIN_IN_FLIGHT = 24, 4
TOKENS_PER_AGENT = 190
RETRIES_429 = 3
BATCH_SYSTEM = SYSTEM + """

BATCH MODE. The user message describes several occupants, each section starting with "### OCCUPANT <agent id>".
Decide for each one independently, using only that occupant's own cues, beliefs and option ids.
Return exactly one JSON object: {"decisions": [{"agent_id": "<id>", "padm": {...}, "say": ..., "feel": ...,
"confidence": ...}, ...]} with one entry per occupant, in the same order."""


class Limiter:
    """Concurrency limit that backs off on 429 (x0.7, floor 4) and creeps back up on success (AIMD)."""

    def __init__(self, limit: int = MAX_IN_FLIGHT) -> None:
        self.limit, self.active = float(limit), 0
        self.cond = asyncio.Condition()

    async def __aenter__(self) -> None:
        async with self.cond:
            await self.cond.wait_for(lambda: self.active < int(self.limit))
            self.active += 1

    async def __aexit__(self, *exc: object) -> None:
        async with self.cond:
            self.active -= 1
            self.cond.notify_all()

    def backoff(self) -> None:
        self.limit = max(MIN_IN_FLIGHT, self.limit * 0.7)

    def ok(self) -> None:
        self.limit = min(MAX_IN_FLIGHT, self.limit + 0.25)


def _r(x: object, step: float) -> object:
    return round(float(x) / step) * step if isinstance(x, (int, float)) and not isinstance(x, bool) else x


def state_key(inp: dict, st: dict) -> str:
    """What the decision depends on, minus who and where exactly: persona, traits, cues (ids kept so a shared
    answer's evidence ids stay valid), beliefs, offered options, waiting limits, rounded state."""
    agent = inp["agent"]
    traits = {k: _r(v, 10) if k.startswith("t_since") else v for k, v in (agent.get("traits") or {}).items()}
    percepts = [{k: _r(v, 2.0) if k == "dist_m" else _r(v, 10.0) if k == "t_since_s" else v
                 for k, v in p.items()} for p in st["percepts"]]
    beliefs = [(b["id"], b["key"], b["value"], round(b["conf"], 1)) for b in st["beliefs_after"]]
    options = [{k: _r(v, 5.0) if k in ("route_m", "queue_s") else v for k, v in o.items()} for o in st["offered"]]
    lim = st["limits"]
    blob = {"persona": agent.get("persona"), "model": model_for(agent), "traits": traits,
            "known_exits": agent.get("known_exits"), "trigger": inp["trigger"].get("kind"), "percepts": percepts,
            "beliefs": beliefs, "options": options, "limits": [lim["overdue"], lim["allowed_waits"]],
            "appraisal": {k: round(v, 1) for k, v in st["appraisal"].items()},
            "emotion": {k: round(v, 1) for k, v in st["emotion"].items()}}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()[:16]


def batch_sizes(n: int) -> list[int]:
    """n unique states -> call sizes: 1 each up to BATCH_OVER, else ceil(n / 10) near-equal groups."""
    if n <= BATCH_OVER:
        return [1] * n
    k = math.ceil(n / BATCH_MAX)
    return [n // k + (1 if i < n % k else 0) for i in range(k)]


def _fallback_res(inp: dict, st: dict, status: str, errors: list[str]) -> dict:
    sha = hashlib.sha256(json.dumps(prompt_for(inp, st)).encode()).hexdigest()
    return {"prompt_sha256": sha, "raw_output": "", "latency_ms": 0, "cost_usd": 0.0,
            "decision": fallback(inp, st), "validation": {"status": status, "errors": errors}, "source": "fallback"}


class Live:
    def __init__(self, batching: bool = True, max_in_flight: int = MAX_IN_FLIGHT) -> None:
        self.batching = batching
        self.max_in_flight = max_in_flight
        self._ids = itertools.count(1)
        self._done: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._expired: set[str] = set()
        self._prefetch: dict[str, dict] = {}  # state key -> {"res": dict | None, "waiting": [...]}
        self._loop: asyncio.AbstractEventLoop | None = None
        self.calls = 0

    # ---------- background loop ----------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True, name="cognition-live").start()
            fut = asyncio.run_coroutine_threadsafe(self._setup(), self._loop)
            fut.result()
        return self._loop

    async def _setup(self) -> None:
        self.client = httpx.AsyncClient(limits=httpx.Limits(max_connections=self.max_in_flight,
                                                            max_keepalive_connections=self.max_in_flight))
        self.limiter = Limiter(self.max_in_flight)

    def close(self) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.client.aclose(), self._loop).result(timeout=5)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None

    # ---------- public API ----------
    def submit(self, views: list[dict]) -> list[str]:
        """Prepare state now (deterministic, caller thread), send LLM work to the background loop."""
        if not _ENV_DONE[0]:
            configure(reset=False)
        handles, groups = [], {}
        for v in views:
            h = f"h{next(self._ids)}_{v['agent']['id']}_{v['tick']}"
            handles.append(h)
            hit = REPLAY.get((int(v["tick"]), v["agent"]["id"]))
            if hit:
                self._done.put((h, None, None, None, hit))
                continue
            inp = build_inputs(v)
            st = compute_state(inp)
            if CONFIG["offline"] or not st["offered"]:
                self._done.put((h, inp, st, _fallback_res(inp, st, "fallback", ["offline" if CONFIG["offline"]
                                                                                   else "no options"]), None))
                continue
            key = state_key(inp, st)
            member = (h, inp, st, bool(v.get("prefetch")))
            with self._lock:
                pre = self._prefetch.get(key)
                if pre is not None and not v.get("prefetch"):
                    self._prefetch.pop(key)
                    if pre["res"] is not None:
                        self._finish(member, {**pre["res"], "prefetched": True})
                    else:
                        pre["waiting"].append(member)
                    continue
            groups.setdefault(key, []).append(member)
        jobs = list(groups.items())
        for key, members in jobs:
            if all(m[3] for m in members):
                with self._lock:
                    self._prefetch.setdefault(key, {"res": None, "waiting": []})
        if jobs:
            loop = self._ensure_loop()
            sizes = batch_sizes(len(jobs)) if self.batching else [1] * len(jobs)
            start = 0
            for n in sizes:
                chunk = jobs[start:start + n]
                start += n
                asyncio.run_coroutine_threadsafe(self._run(chunk), loop)
        return handles

    def poll(self) -> list[dict]:
        """Finished decisions, committed to records and agent memory (in submit order per agent)."""
        out = []
        while True:
            try:
                h, inp, st, res, hit = self._done.get_nowait()
            except queue.Empty:
                return out
            with self._lock:
                if h in self._expired:
                    self._expired.discard(h)
                    continue
            rec = hit if hit is not None else _record(inp, st, res)
            _remember(rec)
            out.append({"handle": h, "agent_id": rec["agent_id"], "tick": rec["tick"], **_result(rec)})

    def expire(self, handle: str, view: dict) -> dict:
        """Deadline passed: commit the persona fallback now (logged as fallback_deadline); drop the late answer."""
        with self._lock:
            self._expired.add(handle)
        inp = build_inputs(view)
        st = compute_state(inp)
        rec = _record(inp, st, _fallback_res(inp, st, "fallback_deadline", ["deadline passed"]))
        _remember(rec)
        return {"handle": handle, "agent_id": rec["agent_id"], "tick": rec["tick"], **_result(rec)}

    def wait(self, n: int, timeout: float = 30.0) -> list[dict]:
        """Block until n results are ready (tests and benchmarks; the live loop uses poll)."""
        out, t_end = [], time.monotonic() + timeout
        while len(out) < n and time.monotonic() < t_end:
            out += self.poll()
            if len(out) < n:
                time.sleep(0.01)
        return out

    async def adecide(self, views: list[dict], timeout: float = 30.0) -> list[dict]:
        """asyncio twin for an async sim loop: submit, then await every handle."""
        handles = self.submit(views)
        got: dict[str, dict] = {}
        t_end = time.monotonic() + timeout
        while len(got) < len(handles) and time.monotonic() < t_end:
            got.update({r["handle"]: r for r in self.poll()})
            await asyncio.sleep(0.01)
        return [got[h] for h in handles if h in got]

    # ---------- workers ----------
    def _finish(self, member: tuple, res: dict) -> None:
        h, inp, st, _ = member
        self._done.put((h, inp, st, res, None))

    def _deliver(self, key: str, members: list[tuple], res: dict) -> None:
        """Leader's result to every member; a prefetch-only group parks the result for a later submit."""
        leader = members[0][1]["agent"]["id"]
        live = [m for m in members if not m[3]]
        for m in live:
            shared = {"shared_with": leader} if m[1]["agent"]["id"] != leader else {}
            self._finish(m, {**res, **shared})
        with self._lock:
            pre = self._prefetch.get(key)
            if pre is not None and not live:
                pre["res"] = res
                for m in pre.pop("waiting", []):
                    self._finish(m, {**res, "prefetched": True})
                pre["waiting"] = []

    async def _call(self, msgs: list[dict], model: str, max_tokens: int, tag: str = "") -> tuple[str, dict]:
        for attempt in range(RETRIES_429 + 1):
            try:
                async with self.limiter:
                    out = await llm.achat(self.client, msgs, model=model, temperature=CONFIG["temperature"],
                                          max_tokens=max_tokens, tag=tag, timeout=CONFIG["timeout_s"])
                self.limiter.ok()
                if not out[1]["cached"]:
                    self.calls += 1
                return out
            except llm.RateLimited as e:
                self.limiter.backoff()
                if attempt == RETRIES_429:
                    raise
                await asyncio.sleep(min(4.0, e.retry_after) * (1 + attempt))
        raise RuntimeError("unreachable")

    async def _single(self, inp: dict, st: dict) -> dict:
        """Async twin of decide.deliberate: one call, one repair, then the persona fallback."""
        msgs = prompt_for(inp, st)
        out = {"prompt_sha256": hashlib.sha256(json.dumps(msgs).encode()).hexdigest(), "raw_output": "",
               "latency_ms": 0, "cost_usd": 0.0}
        errors: list[str] = []
        for attempt in range(2):
            try:
                raw, meta = await self._call(msgs, model_for(inp["agent"]), 260)
            except Exception as e:  # timeout, 402/429/5xx, no key: record and fall back
                errors.append(f"llm: {type(e).__name__}: {str(e)[:120]}")
                break
            out.update(raw_output=raw, latency_ms=out["latency_ms"] + meta["latency_ms"],
                       cost_usd=out["cost_usd"] + meta["cost_usd"])
            dec, errs = validate(raw, st["ids"], st["offered"], *validate_ctx(inp, st))
            if dec:
                return {**out, "decision": dec, "source": "llm",
                        "validation": {"status": "repaired" if attempt else "ok", "errors": errors}}
            errors += errs
            msgs = msgs + [{"role": "assistant", "content": raw},
                           {"role": "user", "content": "That answer is invalid: " + "; ".join(errs) +
                            ". Return the corrected JSON object only."}]
        return {**_fallback_res(inp, st, "fallback", errors), **{k: out[k] for k in ("latency_ms", "cost_usd")}}

    async def _run(self, chunk: list[tuple[str, list[tuple]]]) -> None:
        try:
            if len(chunk) == 1:
                key, members = chunk[0]
                self._deliver(key, members, await self._single(members[0][1], members[0][2]))
                return
            for (key, members), res in zip(chunk, await self._batch([m[0] for _, m in chunk])):
                self._deliver(key, members, res)
        except Exception as e:  # never lose a handle: anything unexpected becomes a logged fallback
            for key, members in chunk:
                inp, st = members[0][1], members[0][2]
                self._deliver(key, members, _fallback_res(inp, st, "fallback", [f"live: {type(e).__name__}: {e}"]))

    async def _batch(self, leaders: list[tuple]) -> list[dict]:
        """One call for several agents; each entry is validated against its own agent's state."""
        users = [f"### OCCUPANT {m[1]['agent']['id']}\n{prompt_for(m[1], m[2])[1]['content']}" for m in leaders]
        msgs = [{"role": "system", "content": BATCH_SYSTEM}, {"role": "user", "content": "\n\n".join(users)}]
        sha = hashlib.sha256(json.dumps(msgs).encode()).hexdigest()
        batch_id = f"b_{sha[:10]}"
        try:
            raw, meta = await self._call(msgs, model_for(leaders[0][1]["agent"]), TOKENS_PER_AGENT * len(leaders))
            entries = llm.parse_json(raw).get("decisions") or []
        except Exception as e:  # whole batch failed: each agent gets its own single call
            entries, meta, raw = [], {"latency_ms": 0, "cost_usd": 0.0}, f"batch failed: {type(e).__name__}"
        by_id = {str(x.get("agent_id")): x for x in entries if isinstance(x, dict)}
        out = []
        for i, (_, inp, st, _p) in enumerate(leaders):
            entry = by_id.get(inp["agent"]["id"]) or (entries[i] if i < len(entries) and not by_id else None)
            info = {"batch": {"id": batch_id, "size": len(leaders), "index": i}}
            if entry is not None:
                one = json.dumps({k: v for k, v in entry.items() if k != "agent_id"})
                dec, errs = validate(one, st["ids"], st["offered"], *validate_ctx(inp, st))
                if dec:
                    out.append({"prompt_sha256": sha, "raw_output": one, "decision": dec, "source": "llm",
                                "validation": {"status": "ok", "errors": []}, "latency_ms": meta["latency_ms"],
                                "cost_usd": meta["cost_usd"] / len(leaders), **info})
                    continue
            else:
                errs = [f"no entry for {inp['agent']['id']} in batch answer"]
            res = await self._single(inp, st)  # per-agent repair path for the bad entry
            if res["validation"]["status"] == "ok":
                res["validation"]["status"] = "repaired"
            res["validation"]["errors"] = errs + res["validation"]["errors"]
            out.append({**res, **info})
        return out
