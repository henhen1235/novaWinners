"""Lockstep evacuation loop (dt = 0.5 s) with a pluggable decide() hook.

Per tick: events, cue noticing, perception, apply decisions due now, ask for
new decisions (applied at tick + DECISION_DELAY_TICKS), move, exit, record a
frame. Times in s, distances in m.
"""

from __future__ import annotations

import logging
import math
from typing import Callable

import numpy as np

from sim import perception
from sim.agents import Agent, spawn
from sim.movement import resolve_moves
from sim.navmesh import INF
from sim.policy import decide as default_decide
from sim.world import (DEFAULT_BLOCK_AFTER_S, DEFAULT_RUMOR_RADIUS_M, FIRE_COST, SMOKE_COST, Event,
                       World)

log = logging.getLogger("sim")

DT = 0.5
DECISION_DELAY_TICKS = 2
DECISION_COOLDOWN_TICKS = 4
PERIODIC_S = {"CONTINUE_TASK": 20.0, "SEEK_INFO": 10.0}
EXIT_RADIUS_M = 0.5
WAYPOINT_EPS_M = 1e-3  # larger values cut corners right after a replan
STUCK_TICKS = 6
PRIORITY = {"hazard_seen": 5, "path_blocked": 5, "rumor_heard": 4, "heard": 4, "cue_noticed": 3,
            "premove_done": 3, "door_reached": 2, "periodic": 1}

Decider = Callable[[dict], dict]


class Sim:
    """One run: scene + scenario + seed + decider -> frames, events, decision records."""

    def __init__(self, scene: dict, scenario: dict, seed: int, decider: Decider = default_decide,
                 decide_batch: Callable[[list[dict]], list[dict]] | None = None,
                 omniscient: bool = False, run_id: str = "run") -> None:
        self.world = World(scene, DT)
        self.scenario = scenario
        self.seed = seed
        self.decider = decider
        self.decide_batch = decide_batch
        self.omniscient = omniscient
        self.run_id = run_id
        self.duration = float(scenario.get("duration_s", 300))
        self.agents, self.pos = spawn(self.world, scenario.get("occupants", []), seed, omniscient)
        self.events = sorted((self.world.resolve_event(e) for e in scenario.get("events", [])),
                             key=lambda e: (e.t_start, e.id))
        self.alarm_t = self._alarm_time()
        self.tick = 0
        self.t = 0.0
        self.frames: list[dict] = []
        self.replay_events: list[dict] = []
        self.records: list[dict] = []
        self.pending: dict[int, list[tuple[Agent, dict, dict]]] = {}
        self.crossings: list[tuple[float, str, str]] = []  # (t, door or exit id, agent id)
        self.msg_seq = 0
        self.llm_calls = 0
        self.cost_usd = 0.0

    def _alarm_time(self) -> float:
        cues = [e.t_start for e in self.events if e.type in ("alarm_only", "fire", "smoke")]
        return min(cues) if cues else 0.0

    # ---- main loop ---------------------------------------------------------
    def run(self) -> None:
        n_ticks = int(round(self.duration / DT))
        for self.tick in range(n_ticks + 1):
            self.t = round(self.tick * DT, 3)
            self.step()
            self._frame()
            if all(a.state == "exited" for a in self.agents):
                break

    def step(self) -> None:
        self._fire_events()
        self._notice_cues()
        perception.sense(self)
        self._timers()
        for agent, result, rec in self.pending.pop(self.tick, []):
            self._apply(agent, result, rec)
        self._decide_all()
        self._move()

    def active(self) -> list[Agent]:
        return [a for a in self.agents if a.state != "exited"]

    def trigger(self, a: Agent, kind: str, ref: str | None = None) -> None:
        """Queue a decision point; a higher-priority trigger replaces a pending lower one."""
        if a.state == "exited":
            return
        prio = PRIORITY.get(kind, 1)
        if a.trigger is None or prio > a.trigger_prio:
            a.trigger, a.trigger_prio = {"kind": kind, "ref": ref}, prio

    def event(self, kind: str, agent: str | None = None, ref: str | None = None, text: str | None = None) -> None:
        self.replay_events.append({"t": self.t, "kind": kind, "agent": agent, "ref": ref, "text": text})

    # ---- events -------------------------------------------------------------
    def _fire_events(self) -> None:
        for ev in self.events:
            if ev.t_start > self.t + 1e-9:
                break
            if ev.type in ("fire", "smoke"):
                self._hazard_phase(ev)
            elif ev.phase == 0:
                ev.phase = 1
                {"blocked_exit": self._block_exit, "rumor": self._rumor}.get(ev.type, self._other_event)(ev)

    def _hazard_phase(self, ev: Event) -> None:
        block_at = ev.t_start + float(ev.params.get("block_after_s", DEFAULT_BLOCK_AFTER_S))
        where = ev.room or "the area"
        if ev.phase == 0:
            ev.phase = 1
            self.world.set_cost(ev.tris, FIRE_COST if ev.type == "fire" else SMOKE_COST)
            self.event("fire_spread", ref=ev.id, text=f"{ev.type} in {where}; routes through it are slow")
            self._replan_all()
        if ev.phase == 1 and self.t >= block_at - 1e-9:
            ev.phase = 2
            self.world.set_cost(ev.tris, INF)
            self.event("fire_spread", ref=ev.id, text=f"{ev.type} has made {where} impassable")
            for d in self.world.doors:
                if ev.room in d.connects:
                    self.event("blocked", ref=d.id, text=f"door {d.id} cut off by {ev.type}")
            self._replan_all()

    def _block_exit(self, ev: Event) -> None:
        if ev.exit in self.world.exits:
            self.world.exits[ev.exit].blocked = True
            self.event("blocked", ref=ev.exit, text=f"exit {ev.exit} blocked")
            if self.omniscient:
                for a in self.active():
                    self.trigger(a, "path_blocked", ev.exit)
        elif ev.door:
            self.world.block_door(ev.door)
            self.event("blocked", ref=ev.door, text=f"door {ev.door} blocked")
            self._replan_all()
        else:
            log.warning("blocked_exit %s: no exit or door to block", ev.id)

    def _rumor(self, ev: Event) -> None:
        text = ev.params.get("text", "I heard an exit is blocked.")
        claim = ev.params.get("claim") or {}
        radius = float(ev.params.get("radius_m", DEFAULT_RUMOR_RADIUS_M))
        self.event("rumor", ref=ev.id, text=text)
        if ev.point is None:
            return
        for a in self.active():
            near = np.linalg.norm(self.pos[a.row] - ev.point) <= radius
            if near or (ev.room and self.world.room_at(self.pos[a.row]) == ev.room):
                self.hear(a, None, text, claim, urgent=False, source={"kind": "rumor", "ref": ev.id}, conf=0.6)
                self.trigger(a, "rumor_heard", ev.id)

    def _other_event(self, ev: Event) -> None:
        if ev.type != "alarm_only":
            log.warning("event type %s is not simulated; ignored", ev.type)
        self.event("rumor" if ev.type == "rumor" else "fire_spread", ref=ev.id, text=f"{ev.type} at t={ev.t_start}")

    # ---- beliefs and messages -------------------------------------------------
    def believe(self, a: Agent, key: str, value: bool, conf: float, source: dict) -> bool:
        """Set a belief unless a more trusted one exists (own sight beats hearsay). True if changed."""
        old = a.beliefs.get(key)
        if old and old["source"]["kind"] == "observation" and source["kind"] != "observation":
            return False
        if old and old["value"] == value and old["conf"] >= conf:
            return False
        bid = old["id"] if old else f"b{len(a.beliefs) + 1}"
        a.beliefs[key] = {"id": bid, "key": key, "value": value, "conf": round(conf, 2), "source": source,
                          "tick": self.tick}
        return True

    def hear(self, a: Agent, speaker: Agent | None, text: str, claim: dict, urgent: bool, source: dict,
             conf: float) -> None:
        self.msg_seq += 1
        mid = f"m_{self.msg_seq}"
        a.messages.append({"id": mid, "from": speaker.id if speaker else source.get("ref"), "text": text,
                           "urgent": urgent, "t": self.t, "claim": claim})
        self.event("heard", agent=a.id, ref=mid, text=text)
        blocked = claim.get("exit_blocked")
        for eid in [blocked] if isinstance(blocked, str) else (blocked or []):
            src = dict(source, ref=source.get("ref") or mid)
            if speaker is not None:
                src = {"kind": "utterance", "ref": mid, "from": speaker.id}
            self.believe(a, f"exit_open:{eid}", False, conf, src)
        if a.cue_t is None and urgent:
            a.cue_t = self.t

    # ---- timers and triggers --------------------------------------------------
    def _notice_cues(self) -> None:
        if self.t < self.alarm_t:
            return
        for a in self.active():
            if a.cue_t is None and self.t >= self.alarm_t + a.traits["notice_s"]:
                a.cue_t = self.t
                self.trigger(a, "cue_noticed", "alarm")

    def _timers(self) -> None:
        for a in self.active():
            if a.cue_t is None or a.action not in PERIODIC_S:
                continue
            if not a.premove_fired and self.t - a.cue_t >= a.traits["premove_s"]:
                a.premove_fired = True
                self.trigger(a, "premove_done", None)
            elif self.t >= a.next_periodic_t:
                self.trigger(a, "periodic", None)

    # ---- decisions --------------------------------------------------------------
    def _decide_all(self) -> None:
        ready = [a for a in self.active() if a.trigger and not a.in_flight
                 and self.tick - a.last_decide_tick >= DECISION_COOLDOWN_TICKS and a.cue_t is not None]
        if not ready:
            return
        views = [perception.build_view(self, a) for a in ready]
        results = self._call_decider(views)
        for a, view, res in zip(ready, views, results):
            rec = self._record(a, view, res)
            a.in_flight, a.last_decide_tick, a.trigger, a.trigger_prio = True, self.tick, None, 0
            a.next_periodic_t = 1e18
            self.pending.setdefault(self.tick + DECISION_DELAY_TICKS, []).append((a, res, rec))

    def _call_decider(self, views: list[dict]) -> list[dict]:
        if self.decide_batch is not None:
            try:
                out = list(self.decide_batch(views))
                if len(out) == len(views):
                    return [self._checked(v, r) for v, r in zip(views, out)]
            except Exception as exc:  # decider bugs must not kill the run
                log.warning("decide_batch failed (%s); using per-agent decide", exc)
        return [self._safe_decide(v) for v in views]

    def _safe_decide(self, view: dict) -> dict:
        try:
            return self._checked(view, self.decider(view))
        except Exception as exc:
            log.warning("decide failed for %s (%s); default policy used", view["agent"]["id"], exc)
            return _fallback(view, [f"exception: {exc}"])

    def _checked(self, view: dict, res: dict) -> dict:
        ids = {o["id"] for o in view["options"]}
        if not isinstance(res, dict) or res.get("choice") not in ids:
            return _fallback(view, [f"invalid choice: {res.get('choice') if isinstance(res, dict) else res!r}"])
        return res

    def _record(self, a: Agent, view: dict, res: dict) -> dict:
        rec = {"record_id": f"d_{len(self.records) + 1:06d}", "run_id": self.run_id, "tick": self.tick,
               "t_s": self.t, "agent_id": a.id, "persona": a.persona, "model": a.model,
               "trigger": view["trigger"], "percepts": view["percepts"], "beliefs_before": view["beliefs"],
               "options": view["options"]}
        extra = res.get("record") or {}
        rec.update({k: v for k, v in extra.items() if k not in ("record_id", "run_id", "tick", "t_s", "agent_id")})
        rec.update({"choice": res["choice"], "say": res.get("say"), "apply_tick": self.tick + DECISION_DELAY_TICKS})
        if rec.get("raw_output"):
            self.llm_calls += 1
        self.cost_usd += float(rec.get("cost_usd") or 0.0)
        self.records.append(rec)
        return rec

    def _apply(self, a: Agent, res: dict, rec: dict) -> None:
        a.in_flight = False
        if a.state == "exited":
            return
        opt = next(o for o in rec["options"] if o["id"] == res["choice"])
        self.event("decision", agent=a.id, ref=rec["record_id"], text=_label(opt))
        act = opt["action"]
        if act in ("EVACUATE", "FOLLOW") and opt.get("exit"):
            if opt["exit"] not in a.known_exits:
                a.known_exits.append(opt["exit"])
            a.action = act
            self._go(a, opt["exit"])
        else:
            a.action = act if act in PERIODIC_S else "CONTINUE_TASK"
            if act == "SEEK_INFO" and a.rng.random() < 0.5:
                self.learn_nearest_exit(a)
            a.state, a.waypoints, a.target_exit = "waiting", [], None
            a.next_periodic_t = self.t + PERIODIC_S[a.action]
        say = res.get("say")
        if say and not (a.last_say and a.last_say[0] == say and self.t - a.last_say[1] < 30):
            a.last_say = (say, self.t)
            self.event("said", agent=a.id, ref=rec["record_id"], text=say)
            perception.broadcast(self, a, say, urgent=act in ("EVACUATE", "FOLLOW"))

    def learn_nearest_exit(self, a: Agent) -> str | None:
        """Looking around or asking: the agent learns the closest reachable exit it did not know."""
        tri = int(self.world.nav.locate(self.pos[a.row][None])[0])
        unknown = [e for e in self.world.exits if e not in a.known_exits and math.isfinite(self.world.route_m(e, tri))]
        if not unknown:
            return None
        best = min(unknown, key=lambda e: self.world.route_m(e, tri))
        a.known_exits.append(best)
        return best

    def _go(self, a: Agent, exit_id: str) -> None:
        tri = int(self.world.nav.locate(self.pos[a.row][None])[0])
        path = self.world.path_to_exit(self.pos[a.row].copy(), tri, exit_id)
        a.target_exit = exit_id
        if path is None:
            a.state, a.waypoints = "waiting", []
            a.next_periodic_t = self.t + PERIODIC_S["SEEK_INFO"]
            self.trigger(a, "path_blocked", exit_id)
            return
        a.state, a.waypoints = "moving", path
        if a.first_move_t is None:
            a.first_move_t = self.t

    def _replan_all(self) -> None:
        for a in self.active():
            if a.state in ("moving", "queued") and a.target_exit:
                self._go(a, a.target_exit)

    # ---- movement -------------------------------------------------------------
    def _move(self) -> None:
        for b in [d.bucket for d in self.world.doors] + [e.bucket for e in self.world.exits.values()]:
            b.refill()
        act = self.active()
        if not act:
            return
        rows = np.array([a.row for a in act])
        pos = self.pos[rows]
        self._skip_visible_waypoints(act, pos)
        step = np.zeros_like(pos)
        for i, a in enumerate(act):
            if a.state in ("moving", "queued"):
                step[i] = self._seek(a, pos[i])
        pushable = np.ones(len(act))
        new, denied, passed = resolve_moves(self.world, pos, step, pushable)
        for r, door_id in passed:
            self.crossings.append((self.t, door_id, act[r].id))
            if act[r].action in ("EVACUATE", "FOLLOW"):
                self.trigger(act[r], "door_reached", door_id)
        moved = np.linalg.norm(new - pos, axis=1)
        self.pos[rows] = new
        for i, a in enumerate(act):
            if a.state in ("moving", "queued"):
                self._after_step(a, moved[i], float(np.linalg.norm(step[i])), bool(denied[i]))
        self._exits(act)

    def _skip_visible_waypoints(self, act: list[Agent], pos: np.ndarray) -> None:
        """Drop the current waypoint when the one after it is in clear sight.

        Keeps agents that were pushed past a corner from walking back to it.
        """
        idx = [i for i, a in enumerate(act) if a.state in ("moving", "queued") and len(a.waypoints) >= 2]
        if not idx:
            return
        ok = self.world.nav.clear_segments(pos[idx], np.array([act[i].waypoints[1] for i in idx]))
        for i, clear in zip(idx, ok):
            if clear:
                act[i].waypoints.pop(0)

    def _seek(self, a: Agent, p: np.ndarray) -> np.ndarray:
        """Step (m) toward the current waypoint, dropping waypoints already reached."""
        while a.waypoints and np.linalg.norm(a.waypoints[0] - p) < WAYPOINT_EPS_M:
            a.waypoints.pop(0)
        if not a.waypoints:
            return np.zeros(2)
        d = a.waypoints[0] - p
        n = float(np.linalg.norm(d))
        tri = int(self.world.nav.locate(p[None])[0])
        speed = a.traits["speed_mps"] * (0.7 if self.world.nav.mult[tri] > 1 else 1.0)
        return d if n <= speed * DT else d / n * speed * DT

    def _after_step(self, a: Agent, moved: float, wanted: float, denied: bool) -> None:
        a.state = "queued" if denied or (wanted > 0 and moved < 0.3 * wanted) else "moving"
        a.still_ticks = a.still_ticks + 1 if moved < 0.02 and not denied else 0
        if a.still_ticks >= STUCK_TICKS and self.tick - a.replan_tick >= STUCK_TICKS and a.target_exit:
            a.replan_tick, a.still_ticks = self.tick, 0
            self._go(a, a.target_exit)

    def _exits(self, act: list[Agent]) -> None:
        for a in sorted(act, key=lambda a: a.row):
            if a.state not in ("moving", "queued"):
                continue
            p = self.pos[a.row]
            for eid, ex in self.world.exits.items():
                if ex.blocked or np.linalg.norm(p - ex.goal) > EXIT_RADIUS_M:
                    continue
                if ex.bucket.take():
                    a.state, a.exit_t, a.exit_id, a.waypoints = "exited", self.t, eid, []
                    self.crossings.append((self.t, eid, a.id))
                    self.event("exited", agent=a.id, ref=eid, text=f"left by {ex.label}")
                else:
                    a.state = "queued"
                break

    def _frame(self) -> None:
        rows = []
        for a in self.agents:
            x, z = self.pos[a.row]
            state = "deciding" if a.in_flight and a.state == "waiting" else a.state
            rows.append([a.id, round(float(x), 2), round(float(z), 2), state])
        self.frames.append({"t": self.t, "a": rows})


def _label(opt: dict) -> str:
    if opt.get("exit"):
        return f"{opt['action']} {opt['exit']}" + (f" (following {opt['target']})" if opt.get("target") else "")
    return opt["action"]


def _fallback(view: dict, errors: list[str]) -> dict:
    res = default_decide(view)
    res["record"]["validation"] = {"status": "fallback", "errors": errors}
    return res

