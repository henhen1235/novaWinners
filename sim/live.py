"""Live mode pieces (docs/LIVE_MODE.md): branch from a decision log, async decisions, paced loop.

A branch re-runs ticks 0..t with the recorded decisions (no decider calls), applies edits at
t, then continues with the live decider. Async decisions never block the clock: each request
runs in a thread pool and applies at the first tick after it returns, or the persona fallback
applies after DEADLINE_TICKS (logged as validation.status = "fallback_deadline"). Times in s.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from sim.engine import DT, Sim
from sim.policy import decide as default_decide

log = logging.getLogger("sim")

DEADLINE_TICKS = 4  # 2 s of sim time
MAX_STALL_S = 1.0  # how long the paced loop may wait for an answer about to miss its deadline


def load_records(path: str | Path) -> list[dict]:
    """DecisionRecords from a decisions.jsonl file (or a run directory containing one)."""
    p = Path(path)
    p = p / "decisions.jsonl" if p.is_dir() else p
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def branch(scene: dict, scenario: dict, seed: int, records: list[dict], t_branch: float,
           events: list[dict] | None = None, occupants: list[dict] | None = None,
           decider: Callable[[dict], dict] = default_decide, decide_batch: Callable | None = None,
           run_id: str = "branch", omniscient: bool = False, history: list[dict] | None = None) -> Sim:
    """A Sim replayed to t_branch from `records`, with edits applied for the next tick.

    With no edits, running the returned Sim to the end reproduces the recorded run exactly
    (given the same decider for the live part when it is deterministic). `history` holds
    earlier live edits ({"t_s", "events", "occupants"}) that are re-applied at their ticks.
    """
    sim = Sim(scene, scenario, seed, decider, decide_batch, omniscient=omniscient, run_id=run_id)
    sim.replay = {(int(r["tick"]), r["agent_id"]): r for r in records}
    sim.replay_until = max(0, int(round(t_branch / DT)))
    todo = sorted(history or [], key=lambda x: x["t_s"])
    while sim.tick < sim.replay_until:
        while todo and int(round(todo[0]["t_s"] / DT)) <= sim.tick:
            past = todo.pop(0)
            sim.add_events(copy.deepcopy(past.get("events", [])))
            sim.add_occupants(copy.deepcopy(past.get("occupants", [])))
        if not sim.advance():
            break
    if events:
        sim.add_events(events)
    if occupants:
        sim.add_occupants(occupants)
    return sim


class AsyncDecisions:
    """Per-agent decide() calls in a thread pool; results apply within DEADLINE_TICKS or fall back."""

    def __init__(self, decide: Callable[[dict], dict], workers: int = 24,
                 deadline_ticks: int = DEADLINE_TICKS) -> None:
        self.decide = decide
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="decide")
        self.deadline = deadline_ticks
        self.waiting: list[tuple[Future, object, dict, int]] = []
        self.stats = {"requested": 0, "applied": 0, "fallback_deadline": 0, "errors": 0, "latency_s": []}

    def _timed(self, view: dict) -> tuple[dict, float]:
        t0 = time.perf_counter()
        return self.decide(view), time.perf_counter() - t0

    def submit(self, sim: Sim, agents: list, views: list[dict]) -> None:
        for a, v in zip(agents, views):
            self.waiting.append((self.pool.submit(self._timed, v), a, v, sim.tick))
            self.stats["requested"] += 1

    def poll(self, sim: Sim) -> None:
        """Apply answers that came back; past the deadline apply the persona fallback (late answers dropped)."""
        keep = []
        for fut, a, view, req in self.waiting:
            if fut.done():
                try:
                    res, lat = fut.result()
                    self.stats["latency_s"].append(lat)
                except Exception as exc:  # a failing decider must not stop the clock
                    log.warning("async decide failed for %s: %s", a.id, exc)
                    self.stats["errors"] += 1
                    res = default_decide(view)
                sim.finish_async(a, view, res, req)
                self.stats["applied"] += 1
            elif sim.tick - req >= self.deadline:
                fut.cancel()
                sim.finish_async(a, view, default_decide(view), req, status="fallback_deadline")
                self.stats["fallback_deadline"] += 1
            else:
                keep.append((fut, a, view, req))
        self.waiting = keep

    def about_to_miss(self, tick: int) -> list[Future]:
        return [f for f, _, _, req in self.waiting if not f.done() and tick + 1 - req >= self.deadline]

    def stall(self, tick: int, timeout: float) -> float:
        """Wait (at most timeout s) for answers that would miss their deadline next tick; returns s waited."""
        late = self.about_to_miss(tick)
        if not late:
            return 0.0
        from concurrent.futures import wait
        t0 = time.perf_counter()
        wait(late, timeout=timeout)
        return time.perf_counter() - t0

    def pending(self) -> int:
        return len(self.waiting)

    def summary(self) -> dict:
        lat = sorted(self.stats["latency_s"])
        n = self.stats["requested"] or 1
        return {k: v for k, v in self.stats.items() if k != "latency_s"} | {
            "fallback_rate": round(self.stats["fallback_deadline"] / n, 3),
            "latency_p50_s": round(lat[len(lat) // 2], 3) if lat else None,
            "latency_p90_s": round(lat[int(len(lat) * 0.9)], 3) if lat else None}

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


class CognitionLive:
    """cognition.live.Live behind the AsyncDecisions interface: non-blocking submit, poll, deadline expire.

    Also prefetches the landing decision for people within PREFETCH_S of the end of a stair flight.
    """

    PREFETCH_S = 2.0

    def __init__(self, deadline_ticks: int = DEADLINE_TICKS, max_in_flight: int = 24) -> None:
        from cognition.live import Live
        self.live = Live(batching=False, max_in_flight=max_in_flight)
        self.deadline = deadline_ticks
        self.waiting: dict[str, tuple[object, dict, int, float]] = {}
        self.stats = {"requested": 0, "applied": 0, "fallback_deadline": 0, "errors": 0, "prefetched": 0,
                      "latency_s": []}

    def submit(self, sim: Sim, agents: list, views: list[dict]) -> None:
        t0 = time.perf_counter()
        for h, a, v in zip(self.live.submit(views), agents, views):
            self.waiting[h] = (a, v, sim.tick, t0)
            self.stats["requested"] += 1

    @staticmethod
    def _res(r: dict) -> dict:
        return {k: r[k] for k in ("choice", "say", "record", "pace") if k in r}

    def poll(self, sim: Sim) -> None:
        now = time.perf_counter()
        for r in self.live.poll():
            item = self.waiting.pop(r["handle"], None)
            if item is None:
                continue
            a, view, req, t0 = item
            self.stats["latency_s"].append(now - t0)
            sim.finish_async(a, view, self._res(r), req)
            self.stats["applied"] += 1
        for h, (a, view, req, _) in list(self.waiting.items()):
            if sim.tick - req >= self.deadline:
                self.waiting.pop(h)
                sim.finish_async(a, view, self._res(self.live.expire(h, view)), req, status="fallback_deadline")
                self.stats["fallback_deadline"] += 1
        self._prefetch(sim)

    def _prefetch(self, sim: Sim) -> None:
        from sim import perception
        from sim.pace import stair_pace_speed
        for a in sim.active():
            st = a.stair
            if st is None or st.get("prefetched"):
                continue
            left_s = (st["link"].length_m - st["prog"]) / max(stair_pace_speed(a, st["link"].down), 0.1)
            if left_s <= self.PREFETCH_S:
                st["prefetched"] = True
                v = perception.build_view(sim, a)
                v["trigger"] = {"kind": "level_reached", "ref": sim.world.nav.level_ids[st["link"].b_lv]}
                v["prefetch"] = True
                self.live.submit([v])
                self.stats["prefetched"] += 1

    def stall(self, tick: int, timeout: float) -> float:
        late = [h for h, (_, _, req, _) in self.waiting.items() if tick + 1 - req >= self.deadline]
        if not late:
            return 0.0
        t0 = time.perf_counter()
        time.sleep(min(timeout, 0.25))  # answers land in Live's queue; the next poll picks them up
        return time.perf_counter() - t0

    def pending(self) -> int:
        return len(self.waiting)

    summary = AsyncDecisions.summary

    def close(self) -> None:
        self.live.close()


class Pacer:
    """Keeps sim time at `speed` x wall time; slows (warp < speed) instead of dropping decisions."""

    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed
        self.t_wall0 = time.perf_counter()
        self.sim_t0 = 0.0
        self.stalled_s = 0.0

    def reset(self, sim_t: float) -> None:
        self.t_wall0, self.sim_t0 = time.perf_counter(), sim_t

    def wait(self, sim: Sim) -> float:
        """Sleep until the next tick is due (and briefly for answers near their deadline); returns warp."""
        if sim.async_dec is not None:
            waited = sim.async_dec.stall(sim.tick, MAX_STALL_S)
            if waited:
                self.stalled_s += waited
                self.reset(sim.t)
        due = self.t_wall0 + (sim.t + DT - self.sim_t0) / max(self.speed, 1e-3)
        now = time.perf_counter()
        if due > now:
            time.sleep(due - now)
        wall = time.perf_counter() - self.t_wall0
        return round((sim.t + DT - self.sim_t0) / wall, 2) if wall > 0 else self.speed


def run_live(sim: Sim, speed: float = 1.0, on_tick: Callable[[Sim, float], None] | None = None) -> None:
    """Run a Sim paced at `speed` x real time, calling on_tick(sim, warp) after every tick."""
    pacer = Pacer(speed)
    pacer.reset(sim.t)
    while sim.advance():
        warp = pacer.wait(sim)
        if on_tick:
            on_tick(sim, warp)
