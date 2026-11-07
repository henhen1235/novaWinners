"""Live sim server: `python -m sim.serve --port 8790` (docs/LIVE_MODE.md, wire format in docs/SIM_INTERFACES.md).

One WebSocket session per connection at /ws. The client sends JSON control messages
(start, pause, resume, speed, edit, branch, end); the server streams binary frames (at most
FRAME_HZ) plus JSON meta/events/status. On end (or when the run finishes) the branch is
written as a normal run directory (replay.json, decisions.jsonl, metrics.json, edits.json).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import struct
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from sim.engine import DT, Sim
from sim.live import MAX_STALL_S, AsyncDecisions, CognitionLive, branch
from sim.output import write_run

log = logging.getLogger("sim")

STATES = ["waiting", "moving", "deciding", "queued", "exited", "helping", "hiding", "waiting_at_refuge",
          "carried", "at_elevator_lobby"]
STATE_CODE = {s: i for i, s in enumerate(STATES)}
FRAME_HZ = 5.0
STATUS_EVERY_S = 1.0
RUNS_DIR = Path(os.environ.get("NOVA_LIVE_RUNS", "out/live"))

app = FastAPI(title="nova live sim")


def encode_frame(sim: Sim) -> bytes:
    """Little-endian: float32 t, uint32 n, float32[2n] (x, z), uint8[n] state, uint8[n] level index."""
    n = len(sim.agents)
    states = np.array([STATE_CODE.get(_state(a), 0) for a in sim.agents], dtype=np.uint8)
    xz = sim.pos.astype("<f4").reshape(-1)
    return struct.pack("<fI", float(sim.t), n) + xz.tobytes() + states.tobytes() + sim.lv.astype(np.uint8).tobytes()


def _state(a) -> str:
    return "deciding" if a.in_flight and a.state == "waiting" else a.state


def _model_errors() -> int:
    try:
        from cognition import llm
        return int(llm.STATS.get("errors", 0))
    except Exception:
        return 0


def _public_logs(records: list[dict]) -> list[dict]:
    """The clickable agent log: who decided, which model, and why. Not the full prompt."""
    out = []
    for rec in records:
        dec = rec.get("decision") if isinstance(rec.get("decision"), dict) else {}
        padm = dec.get("padm") if isinstance(dec.get("padm"), dict) else {}
        chosen = next((o for o in rec.get("options") or [] if isinstance(o, dict) and o.get("id") == rec.get("choice")), {})
        why = padm.get("why") or chosen.get("action") or ""
        validation = rec.get("validation") if isinstance(rec.get("validation"), dict) else {}
        errors = [str(e) for e in (validation.get("errors") or [])]
        src = rec.get("source")
        status = str(validation.get("status") or "")
        if src == "llm" or (rec.get("raw_output") and not status.startswith("fallback")):
            shown = "model"
        elif src in ("fallback", "fallback_deadline") or status.startswith("fallback"):
            shown = "fallback"
        else:
            shown = "rule"
        item = {
            "record_id": rec.get("record_id"),
            "agent_id": rec.get("agent_id"),
            "t_s": rec.get("t_s"),
            "model": rec.get("model"),
            "persona": rec.get("persona"),
            "say": rec.get("say"),
            "source": shown,
            "note": errors[0] if errors else "",
            "action": chosen.get("action") or "",
            "decision": {"padm": {"why": why}, "reasons": dec.get("reasons") or []},
        }
        if isinstance(rec.get("emotion"), dict):
            item["emotion"] = rec["emotion"]
        out.append(item)
    return out


def _load(ref: str | dict) -> dict:
    return copy.deepcopy(ref) if isinstance(ref, dict) else json.loads(Path(ref).read_text())


def _decider(name: str, no_llm: bool, run_id: str):
    """(decide, async maker or None): 'policy' is the synchronous rule policy; 'cognition' uses
    cognition.live.Live (non-blocking) when present, else cognition.decide in a thread pool."""
    if name == "cognition":
        try:
            import cognition
            cognition.configure(offline=no_llm, run_id=run_id)
        except ImportError as exc:
            log.warning("cognition unavailable (%s); rule policy used", exc)
        else:
            try:
                import cognition.live  # noqa: F401
                return cognition.decide, CognitionLive
            except ImportError:
                return cognition.decide, lambda: AsyncDecisions(cognition.decide)
    from sim.policy import decide
    return decide, None


class Session:
    """One live run: a Sim, its edits, pacing state and the socket it streams to."""

    def __init__(self, ws: WebSocket, msg: dict) -> None:
        self.ws = ws
        self.scene_ref = msg["scene"] if isinstance(msg.get("scene"), str) else "inline"
        self.scene = _load(msg["scene"])
        self.scenario = _load(msg["scenario"])
        self.scenario_ref = msg["scenario"] if isinstance(msg["scenario"], str) else "inline"
        self.seed = int(msg.get("seed", self.scenario.get("seed", 0)))
        self.decide_name, self.no_llm = msg.get("decide", "policy"), bool(msg.get("no_llm", True))
        self.id = msg.get("session") or time.strftime("s%H%M%S")
        self.branch_n = 0
        self.edits: list[dict] = []
        self.speed, self.paused, self.ended = float(msg.get("speed", 1.0)), False, False
        self.queue: list[dict] = []
        self.sim = self._new_sim()
        self.sent_events = 0
        self.sent_agents = 0
        self.sent_logs = 0

    def _new_sim(self, records: list[dict] | None = None, t: float = 0.0) -> Sim:
        run_id = f"{self.id}_b{self.branch_n}"
        decide, make_async = _decider(self.decide_name, self.no_llm, run_id)
        if records is not None:
            sim = branch(self.scene, self.scenario, self.seed, records, t, decider=decide, run_id=run_id,
                         history=self.edits)
        else:
            sim = Sim(self.scene, self.scenario, self.seed, decide, run_id=run_id)
        if make_async is not None:
            sim.async_dec = make_async()
        return sim

    # ---- control messages ------------------------------------------------
    def validate_edit(self, edit: dict) -> list[str]:
        try:
            from scenario.schema import validate
        except ImportError:
            return []
        cand = copy.deepcopy(self.scenario)
        used = {e.get("id") for e in cand.get("events", [])} | {e.get("id") for x in self.edits for e in x.get("events", [])}
        for i, e in enumerate(edit.get("events", [])):
            e.setdefault("id", f"live_{len(self.edits)}_{i}")
            if e["id"] in used:
                e["id"] = f"{e['id']}_{len(self.edits)}"
        cand["events"] = cand.get("events", []) + [e for x in self.edits for e in x.get("events", [])] + edit.get("events", [])
        cand["occupants"] = cand.get("occupants", []) + [o for x in self.edits for o in x.get("occupants", [])] \
            + edit.get("occupants", [])
        return validate(cand, self.scene)

    def apply_edit(self, edit: dict) -> None:
        """Between ticks: events and people join at the next tick."""
        t = round(self.sim.tick * DT, 3)
        for e in edit.get("events", []):
            e["t_start_s"] = max(float(e.get("t_start_s", t)), t)
        self.sim.add_events(edit.get("events", []))
        self.sim.add_occupants(edit.get("occupants", []))
        self.edits.append({"t_s": t, **edit})

    def do_branch(self, t: float, edit: dict) -> None:
        """Re-run 0..t from this branch's decisions, apply the edit at t, continue live."""
        self.close_sim(write=True)
        records = self.sim.records
        self.branch_n += 1
        self.edits = [x for x in self.edits if x["t_s"] < t]  # earlier live edits replay at their ticks
        self.sim = self._new_sim(records, t)
        self.apply_edit(edit)
        self.sent_events = self.sent_agents = self.sent_logs = 0

    def close_sim(self, write: bool) -> Path | None:
        if self.sim.async_dec is not None:
            self.sim.async_dec.close()
        if not write:
            return None
        out = RUNS_DIR / self.id / f"branch_{self.branch_n}"
        write_run(self.sim, out, self.scene_ref, self.scenario_ref)
        (out / "edits.json").write_text(json.dumps(self.edits, indent=1))
        return out

    # ---- streaming -------------------------------------------------------
    async def send_meta(self) -> None:
        nav = self.sim.world.nav
        await self.ws.send_json({"type": "meta", "session": self.id, "branch": self.branch_n, "dt": DT,
                                 "duration_s": self.sim.duration, "levels": nav.level_ids, "states": STATES,
                                 "agents": self._agents(0), "frame": "f32 t, u32 n, f32[2n] xz, u8[n] state, u8[n] level"})
        self.sent_agents = len(self.sim.agents)

    def _agents(self, start: int) -> list[dict]:
        from sim.agents import PERSONA_COLORS
        return [{"id": a.id, "persona": a.persona, "model": a.model, "color": PERSONA_COLORS.get(a.persona, "#888"),
                 "speed": round(float(a.traits.get("speed_mps", 0)), 2),
                 "index": a.row} for a in self.sim.agents[start:]]

    async def send_updates(self, frame: bool) -> None:
        if len(self.sim.agents) > self.sent_agents:
            await self.ws.send_json({"type": "agents", "agents": self._agents(self.sent_agents)})
            self.sent_agents = len(self.sim.agents)
        evs = self.sim.replay_events[self.sent_events:]
        if evs:
            await self.ws.send_json({"type": "events", "events": evs})
            self.sent_events = len(self.sim.replay_events)
        if len(self.sim.records) > self.sent_logs:
            batch = _public_logs(self.sim.records[self.sent_logs:])
            self.sent_logs = len(self.sim.records)
            if batch:
                await self.ws.send_json({"type": "log", "records": batch})
        if frame:
            await self.ws.send_bytes(encode_frame(self.sim))

    def status(self, warp: float, tps: float) -> dict:
        ad = self.sim.async_dec
        return {"type": "status", "t": self.sim.t, "tick": self.sim.tick, "speed": self.speed, "warp": warp,
                "paused": self.paused, "ticks_per_s": round(tps, 2), "decisions": len(self.sim.records),
                "llm_calls": self.sim.llm_calls, "cost_usd": round(self.sim.cost_usd, 6),
                "model_errors": _model_errors(),
                "evacuated": sum(a.exit_t is not None for a in self.sim.agents), "total": len(self.sim.agents),
                "async": ad.summary() if ad else None, "pending": ad.pending() if ad else 0}


async def _stall_for_late(sess: Session) -> float:
    """Slow the clock (instead of dropping answers) when decisions are about to miss their deadline."""
    ad = sess.sim.async_dec
    return await asyncio.to_thread(ad.stall, sess.sim.tick, MAX_STALL_S) if ad else 0.0


async def run_session(sess: Session) -> None:
    """Paced tick loop: apply queued control messages between ticks, stream, keep about `speed` x real time."""
    await sess.send_meta()
    await sess.send_updates(frame=True)
    wall0, sim0, last_frame, ticks, t_rate = time.perf_counter(), sess.sim.t, 0.0, 0, time.perf_counter()
    last_status, status_sim_t = time.perf_counter(), sess.sim.t
    warp = sess.speed
    while not sess.ended:
        while sess.queue:
            msg = sess.queue.pop(0)
            kind = msg.get("type")
            if kind == "pause":
                sess.paused = True
            elif kind == "resume":
                sess.paused, wall0, sim0 = False, time.perf_counter(), sess.sim.t
            elif kind == "speed":
                sess.speed, wall0, sim0 = max(0.1, float(msg.get("value", 1.0))), time.perf_counter(), sess.sim.t
            elif kind in ("edit", "branch"):
                edit = {"events": msg.get("events", []), "occupants": msg.get("occupants", [])}
                errs = sess.validate_edit(edit)
                if errs:
                    await sess.ws.send_json({"type": "error", "for": kind, "errors": errs})
                    continue
                if kind == "branch" and float(msg.get("t", sess.sim.t)) < sess.sim.t:
                    await asyncio.to_thread(sess.do_branch, float(msg["t"]), edit)
                    await sess.send_meta()
                    wall0, sim0 = time.perf_counter(), sess.sim.t
                else:
                    sess.apply_edit(edit)
                await sess.ws.send_json({"type": "edit_ok", "t": sess.sim.t, "edits": len(sess.edits)})
            elif kind == "end":
                sess.ended = True
        if sess.ended:
            break
        if sess.paused or sess.sim.done:
            if sess.sim.done and not sess.paused:
                sess.paused = True
                await sess.ws.send_json({"type": "finished", "t": sess.sim.t})
            await asyncio.sleep(0.05)
            continue
        if await _stall_for_late(sess):
            wall0, sim0 = time.perf_counter(), sess.sim.t
        await asyncio.to_thread(sess.sim.advance)
        ticks += 1
        now = time.perf_counter()
        await sess.send_updates(frame=now - last_frame >= 1.0 / FRAME_HZ or sess.sim.done)
        if now - last_frame >= 1.0 / FRAME_HZ:
            last_frame = now
        due = wall0 + (sess.sim.t - sim0) / sess.speed
        if due > now:
            await asyncio.sleep(due - now)
        now = time.perf_counter()
        if now - last_status >= STATUS_EVERY_S:  # warp: sim seconds per wall second over the last window
            warp = round((sess.sim.t - status_sim_t) / (now - last_status), 2)
            await sess.ws.send_json(sess.status(warp, ticks / max(now - t_rate, 1e-6)))
            last_status, status_sim_t, ticks, t_rate = now, sess.sim.t, 0, now


async def _guarded(sess: Session) -> None:
    """Run the session loop; any crash is logged and reported to the client instead of dying silently."""
    try:
        await run_session(sess)
    except Exception as exc:
        log.exception("live session %s crashed", sess.id)
        try:
            await sess.ws.send_json({"type": "error", "for": "session", "errors": [f"{type(exc).__name__}: {exc}"]})
        except Exception:  # the socket may be gone too
            pass


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    sess: Session | None = None
    loop_task: asyncio.Task | None = None
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "start" and sess is None:
                try:
                    sess = await asyncio.to_thread(Session, ws, msg)
                except Exception as exc:  # bad paths or scenario: report, keep the socket
                    await ws.send_json({"type": "error", "for": "start", "errors": [str(exc)]})
                    continue
                loop_task = asyncio.create_task(_guarded(sess))
            elif sess is not None:
                sess.queue.append(msg)
                if msg.get("type") == "end":
                    await loop_task
                    out = await asyncio.to_thread(sess.close_sim, True)
                    await ws.send_json({"type": "ended", "run_dir": str(out)})
                    sess = None
    except WebSocketDisconnect:
        pass
    finally:
        if sess is not None:
            sess.ended = True
            if loop_task:
                await asyncio.gather(loop_task, return_exceptions=True)
            sess.close_sim(write=True)


def main() -> None:
    import uvicorn
    ap = argparse.ArgumentParser(prog="python -m sim.serve")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
