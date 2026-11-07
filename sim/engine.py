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

from sim import groups, perception, stairs
from sim.agents import Agent, spawn
from sim.movement import resolve_moves
from sim.navmesh import INF, Waypoint
from sim.pace import resolve_pace
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
STAIR_ENTRY_M = 0.6
PRIORITY = {"hazard_seen": 5, "path_blocked": 5, "rumor_heard": 4, "heard": 4, "cue_noticed": 3,
            "premove_done": 3, "level_reached": 4, "picked_up": 5, "stair_smoke": 5, "stair_crowded": 4,
            "route_changed": 4, "help_request": 4, "group_moving": 4, "reached_person": 5,
            "sweep_next": 4, "door_reached": 2, "periodic": 1}
MOVING_ACTIONS = ("EVACUATE", "FOLLOW", "TAKE_STAIRS", "GO_TO_REFUGE", "HELP", "GO_TO_PERSON", "SWEEP")

Decider = Callable[[dict], dict]


class Sim:
    """One run: scene + scenario + seed + decider -> frames, events, decision records."""

    def __init__(self, scene: dict, scenario: dict, seed: int, decider: Decider = default_decide,
                 decide_batch: Callable[[list[dict]], list[dict]] | None = None,
                 omniscient: bool = False, run_id: str = "run",
                 hooks: list[Callable[["Sim"], None]] | None = None) -> None:
        self.world = World(scene, DT)
        self.scenario = scenario
        self.seed = seed
        self.decider = decider
        self.decide_batch = decide_batch
        self.omniscient = omniscient
        self.run_id = run_id
        self.duration = float(scenario.get("duration_s", 300))
        self.agents, self.pos, self.lv = spawn(self.world, scenario.get("occupants", []), seed, omniscient)
        self.groups: dict[str, list[Agent]] = {}
        groups.form(self, self.agents)
        self.events = sorted((self.world.resolve_event(e) for e in scenario.get("events", [])),
                             key=lambda e: (e.t_start, e.id))
        self.alarm_t = self._alarm_time()
        self.hooks = list(hooks or [])  # called at the start of every tick (interventions, loop/ drivers)
        self.signage: list[tuple[str, str, float]] = []  # (room, exit, p) signs seen on the way
        self.assisted = bool((scenario.get("params") or {}).get("assisted_evacuation")) or any(
            it.get("type") == "assisted_evacuation_plan" for it in scenario.get("interventions", []))
        self._apply_interventions(scenario.get("interventions", []))
        self.tick = 0
        self.t = 0.0
        self.frames: list[dict] = []
        self.replay_events: list[dict] = []
        self.records: list[dict] = []
        self.pending: dict[int, list[tuple[Agent, dict, dict]]] = {}
        self.crossings: list[tuple[float, str, str]] = []  # (t, door or exit id, agent id)
        self.stair_moves: list[tuple] = []  # (t, agent id, stair id, from level, to level, action)
        self.msg_seq = 0
        self.llm_calls = 0
        self.cost_usd = 0.0
        self.done = False
        self.replay: dict[tuple[int, str], dict] = {}  # (tick, agent id) -> recorded decision
        self.replay_until = 0  # ticks below this take decisions from `replay`, not the decider
        self.replay_divergence = 0  # replayed decisions that no longer matched the options offered
        self.async_dec = None  # sim.live.AsyncDecisions when decisions must not block the clock
        self.n_ticks = int(round(self.duration / DT))

    def _apply_interventions(self, items: list[dict]) -> None:
        """Static interventions known before the run (signage). Others are events or occupants."""
        for it in items:
            if it.get("type") == "signage" and it.get("exit") in self.world.exits:
                p = float(it.get("p", 0.8))
                if it.get("room"):  # a sign in one room: its occupants now, passers-by in build_view
                    self.signage.append((it["room"], it["exit"], p))
                for a in self.agents:
                    here = not it.get("room") or a.spawn_room == it["room"]
                    if here and it["exit"] not in a.known_exits and a.rng.random() < p:
                        a.known_exits.append(it["exit"])
            elif it.get("type") != "assisted_evacuation_plan":  # that one is read in __init__
                log.warning("intervention %s not supported here; use events/occupants", it.get("type"))

    def _alarm_time(self) -> float:
        cues = [e.t_start for e in self.events if e.type in ("alarm_only", "fire", "smoke", "pa_announcement")]
        return min(cues) if cues else 0.0

    # ---- main loop ---------------------------------------------------------
    def run(self) -> None:
        while self.advance():
            pass

    def advance(self) -> bool:
        """Simulate one tick and record its frame; False once the run is over."""
        if self.done:
            return False
        self.t = round(self.tick * DT, 3)
        self.step()
        self._frame()
        if all(a.state == "exited" for a in self.agents) or self.tick >= self.n_ticks:
            self.done = True
            return False
        self.tick += 1
        return True

    # ---- live edits (sim.live, sim.serve) ----------------------------------------
    def add_events(self, events: list[dict]) -> list[Event]:
        """Scenario-schema events; anything in the past starts at the next tick."""
        out = []
        for e in events:
            ev = self.world.resolve_event(e)
            ev.t_start = max(ev.t_start, round(self.tick * DT, 3))
            out.append(ev)
        self.events = sorted(self.events + out, key=lambda e: (e.t_start, e.id))
        cues = [e.t_start for e in out if e.type in ("alarm_only", "fire", "smoke", "pa_announcement")]
        if cues:
            self.alarm_t = min(self.alarm_t, min(cues))
        return out

    def add_occupants(self, occupants: list[dict]) -> list[Agent]:
        """Scenario-schema occupant groups spawned now (they notice the alarm after their own delay).

        An occupant group may carry "traits" (e.g. speed_mps, premove_s) that override the draws.
        """
        new, pos, lv = spawn(self.world, occupants, self.seed * 1000 + len(self.agents), self.omniscient,
                             start_row=len(self.agents), width=max(3, len(str(len(self.agents) + 999))))
        t0 = round(self.tick * DT, 3)
        for a in new:
            a.spawn_t = t0
        groups.form(self, new)
        self.agents += new
        self.pos = np.vstack([self.pos, pos]) if len(pos) else self.pos
        self.lv = np.concatenate([self.lv, lv]) if len(lv) else self.lv
        for a in new:
            self.event("spawned", agent=a.id, ref=a.persona, text=f"{a.persona} added")
        return new

    def step(self) -> None:
        for hook in self.hooks:
            hook(self)
        self._fire_events()
        self._notice_cues()
        perception.sense(self)
        self._timers()
        for agent, result, rec in self.pending.pop(self.tick, []):
            self._apply(agent, result, rec)
        if self.async_dec is not None:
            self.async_dec.poll(self)
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
                handlers = {"blocked_exit": self._block_exit, "rumor": self._rumor, "open_exit": self._open_exit,
                            "pa_announcement": self._announce}
                handlers.get(ev.type, self._other_event)(ev)

    def _hazard_phase(self, ev: Event) -> None:
        block_at = ev.t_start + float(ev.params.get("block_after_s", DEFAULT_BLOCK_AFTER_S))
        where = ev.room or "the area"
        if ev.phase == 0:
            ev.phase = 1
            self.world.set_cost(ev.tris, FIRE_COST if ev.type == "fire" else SMOKE_COST, ev.stairs)
            self.event("fire_spread", ref=ev.id, text=f"{ev.type} in {where}; routes through it are slow")
            self._replan_all()
        if ev.phase == 1 and self.t >= block_at - 1e-9:
            ev.phase = 2
            self.world.set_cost(ev.tris, INF, ev.stairs)
            self.event("fire_spread", ref=ev.id, text=f"{ev.type} has made {where} impassable")
            for sid in ev.stairs:
                self.event("blocked", ref=sid, text=f"stair {sid} cut off by {ev.type}")
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
        elif ev.stair:
            self.world.block_stair(ev.stair)
            self.event("blocked", ref=ev.stair, text=f"stair {ev.stair} blocked")
            self._replan_all()
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
            if self.lv[a.row] != ev.lv:
                continue
            near = np.linalg.norm(self.pos[a.row] - ev.point) <= radius
            if near or (ev.room and self.world.room_at(self.pos[a.row], ev.lv) == ev.room):
                self.hear(a, None, text, claim, urgent=False, source={"kind": "rumor", "ref": ev.id}, conf=0.6)
                self.trigger(a, "rumor_heard", ev.id)

    def _open_exit(self, ev: Event) -> None:
        if ev.exit in self.world.exits:
            self.world.exits[ev.exit].blocked = False
            self.event("opened", ref=ev.exit, text=f"exit {ev.exit} unlocked")

    def _announce(self, ev: Event) -> None:
        """PA announcement: everyone hears an official, urgent message (with an optional claim)."""
        text = ev.params.get("text", "Please evacuate the building now.")
        self.event("said", ref=ev.id, text=f"PA: {text}")
        for a in self.active():
            self.hear(a, None, text, ev.params.get("claim") or {}, urgent=True,
                      source={"kind": "pa", "ref": ev.id}, conf=0.9, official=True)
            self.trigger(a, "heard", ev.id)

    def _other_event(self, ev: Event) -> None:
        if ev.type != "alarm_only":
            log.warning("event type %s is not simulated; ignored", ev.type)
        self.event("alarm" if ev.type == "alarm_only" else ev.type, ref=ev.id, text=f"{ev.type} at t={ev.t_start} s")

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
             conf: float, official: bool = False) -> None:
        self.msg_seq += 1
        mid = f"m_{self.msg_seq}"
        a.messages.append({"id": mid, "from": speaker.id if speaker else source.get("ref"), "text": text,
                           "urgent": urgent, "official": official, "t": self.t, "claim": claim})
        self.event("heard", agent=a.id, ref=mid, text=text)
        src = dict(source, ref=source.get("ref") or mid)
        if speaker is not None:
            src = {"kind": "utterance", "ref": mid, "from": speaker.id}
        for eid in _ids(claim.get("exit_blocked")):
            self.believe(a, f"exit_open:{eid}", False, conf, src)
        if official:  # PA or staff can clear a rumor: "exit X is open"
            for eid in _ids(claim.get("exit_open")):
                self.believe(a, f"exit_open:{eid}", True, max(conf, 0.9), src)
        if a.cue_t is None and urgent:
            a.cue_t = self.t

    # ---- timers and triggers --------------------------------------------------
    def _notice_cues(self) -> None:
        if self.t < self.alarm_t:
            return
        for a in self.active():
            if a.cue_t is None and self.t >= max(self.alarm_t, a.spawn_t) + a.traits["notice_s"]:
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
        for a in ready:
            a.in_flight, a.last_decide_tick, a.trigger, a.trigger_prio = True, self.tick, None, 0
            a.next_periodic_t = 1e18
        if self.tick < self.replay_until:
            results = [self._replayed(a, v) for a, v in zip(ready, views)]
        elif self.async_dec is not None:
            self.async_dec.submit(self, ready, views)
            return
        else:
            results = self._call_decider(views)
        for a, view, res in zip(ready, views, results):
            rec = self._record(a, view, res, res.get("apply_tick"))
            self.pending.setdefault(rec["apply_tick"], []).append((a, res, rec))

    def _replayed(self, a: Agent, view: dict) -> dict:
        """The recorded decision for this agent and tick (branch re-runs make no decider calls)."""
        rec = self.replay.get((self.tick, a.id))
        mine = next((o for o in view["options"] if rec and o["id"] == rec.get("choice")), None)
        theirs = next((o for o in (rec or {}).get("options", []) if o["id"] == rec.get("choice")), None)
        same = mine is not None and theirs is not None and all(
            mine.get(k) == theirs.get(k) for k in ("action", "exit", "stair", "target"))
        if not same:  # log from another scene/scenario version: the run diverges here
            if self.replay_divergence == 0:
                log.warning("branch replay diverges at tick %d (%s); rule policy from here on", self.tick, a.id)
            self.replay_divergence += 1
            return _fallback(view, ["replay_divergence"])
        keep = {k: v for k, v in rec.items() if k not in _VIEW_KEYS}
        return {"choice": rec["choice"], "say": rec.get("say"), "record": keep, "apply_tick": rec.get("apply_tick")}

    def finish_async(self, a: Agent, view: dict, res: dict, req_tick: int, status: str | None = None) -> None:
        """An async decision (or its deadline fallback) applies at the current tick."""
        res = self._checked(view, res)
        if status:
            res.setdefault("record", {})["validation"] = {"status": status, "errors": []}
        rec = self._record(a, view, res, self.tick, req_tick=req_tick)
        self._apply(a, res, rec)

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

    def _record(self, a: Agent, view: dict, res: dict, apply_tick: int | None = None,
                req_tick: int | None = None) -> dict:
        tick = self.tick if req_tick is None else req_tick
        rec = {"record_id": f"d_{len(self.records) + 1:06d}", "run_id": self.run_id, "tick": tick,
               "t_s": round(tick * DT, 3), "agent_id": a.id, "persona": a.persona, "model": a.model,
               "trigger": view["trigger"], "percepts": view["percepts"], "beliefs_before": view["beliefs"],
               "options": view["options"]}
        extra = res.get("record") or {}
        rec.update({k: v for k, v in extra.items() if k not in ("record_id", "run_id", "tick", "t_s", "agent_id")})
        rec.update({"choice": res["choice"], "say": res.get("say"),
                    "apply_tick": int(apply_tick) if apply_tick is not None else tick + DECISION_DELAY_TICKS})
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
        a.hold, a.pace = False, resolve_pace(res)
        if a.helper:  # being carried: the helper decides the route
            return
        if act == "GO_TO_REFUGE" and a.state == "waiting_at_refuge" and a.target_stair == opt.get("stair"):
            return
        if act in ("EVACUATE", "FOLLOW") and opt.get("exit"):
            if opt["exit"] not in a.known_exits:
                a.known_exits.append(opt["exit"])
            a.action = act
            self.go(a, opt["exit"])
        elif act in ("TAKE_STAIRS", "GO_TO_REFUGE") and opt.get("stair"):
            a.action = act
            self._go_stair(a, opt["stair"], refuge=act == "GO_TO_REFUGE")
        elif act == "HELP" and opt.get("target"):
            a.action = act
            self._go_help(a, opt["target"])
        elif act in ("GO_TO_PERSON", "SWEEP") and opt.get("target"):
            a.action = act
            self._go_person(a, opt["target"])
        else:
            a.action = act if act in PERIODIC_S else "CONTINUE_TASK"
            if act == "SEEK_INFO" and a.rng.random() < 0.5:
                self.learn_nearest_exit(a)
            a.state, a.waypoints, a.target_exit, a.target_stair = "waiting", [], None, None
            a.next_periodic_t = self.t + PERIODIC_S[a.action]
        say = res.get("say")
        if say and not (a.last_say and a.last_say[0] == say and self.t - a.last_say[1] < 30):
            a.last_say = (say, self.t)
            self.event("said", agent=a.id, ref=rec["record_id"], text=say)
            # Chatter from someone who only heard a rumor is a social cue, not an alarm.
            urgent = act in MOVING_ACTIONS and (a.persona == "staff" or bool(a.hazards_seen))
            perception.broadcast(self, a, say, urgent=urgent)

    def tri_of(self, a: Agent) -> int:
        return int(self.world.nav.locate(self.pos[a.row][None], int(self.lv[a.row]))[0])

    def learn_nearest_exit(self, a: Agent) -> str | None:
        """Looking around or asking: the agent learns the closest reachable exit it did not know."""
        tri, ok = self.tri_of(a), a.traits["uses_stairs"]
        unknown = [e for e in self.world.exits
                   if e not in a.known_exits and math.isfinite(self.world.route_m(e, tri, ok))]
        if not unknown:
            return None
        best = min(unknown, key=lambda e: self.world.route_m(e, tri, ok))
        a.known_exits.append(best)
        return best

    def _start(self, a: Agent, path: list | None, ref: str) -> bool:
        if path is None:
            a.state, a.waypoints = "waiting", []
            a.next_periodic_t = self.t + PERIODIC_S["SEEK_INFO"]
            self.trigger(a, "path_blocked", ref)
            return False
        a.state, a.waypoints = "moving", path
        if a.first_move_t is None:
            a.first_move_t = self.t
            groups.on_departure(self, a)
        return True

    def go(self, a: Agent, exit_id: str) -> None:
        """Route an agent to an exit (across levels when it can use stairs)."""
        if a.stair is not None:
            return  # finish the stair first; level_reached re-plans
        path = self.world.path_to_exit(self.pos[a.row].copy(), self.tri_of(a), exit_id,
                                       a.traits["uses_stairs"] or a.carrying)
        a.target_exit, a.target_stair = exit_id, None
        self._start(a, path, exit_id)

    def _go_stair(self, a: Agent, stair_id: str, refuge: bool) -> None:
        """Walk to a stair on this level; take it unless going there as an area of refuge."""
        link = self.world.stair_link(stair_id, int(self.lv[a.row]))
        a.target_exit, a.target_stair = None, stair_id
        if link is None:
            self._start(a, None, stair_id)
            return
        path = self.world.path_to_point(self.pos[a.row].copy(), int(self.lv[a.row]), link.a_pt)
        if path is not None and not refuge:
            path = path + [Waypoint(link.b_pt, link.b_lv, link)]
        self._start(a, path, stair_id)

    def _go_help(self, a: Agent, target_id: str) -> None:
        """Walk to a person waiting for help (stairs allowed); pick-up happens in stairs.update_helpers."""
        p = next((b for b in self.agents if b.id == target_id), None)
        if p is None or p.state == "exited" or (p.helper and p.helper != a.id):
            self._start(a, None, target_id)
            return
        if a.helping != p.id:
            self.event("help_assigned", agent=a.id, ref=p.id, text=f"{a.id} goes to help {p.id}")
        p.helper, a.helping = a.id, p.id
        ta, tb = self.tri_of(a), self.tri_of(p)
        corr = self.world.nav.astar(ta, tb)
        path = None if corr is None else self.world.nav.waypoints(self.pos[a.row].copy(), self.pos[p.row].copy(), corr)
        a.target_exit = a.target_stair = None
        if not self._start(a, path, target_id):
            p.helper, a.helping = None, None

    def _go_person(self, a: Agent, target_id: str) -> None:
        """Walk to another person (stairs allowed); arrival is handled in groups.update."""
        p = next((b for b in self.agents if b.id == target_id), None)
        if p is None or p.state == "exited":
            self._start(a, None, target_id)
            return
        corr = self.world.nav.astar(self.tri_of(a), self.tri_of(p))
        path = None if corr is None else self.world.nav.waypoints(self.pos[a.row].copy(), self.pos[p.row].copy(), corr)
        a.target_exit = a.target_stair = None
        a.going_to = p.id if self._start(a, path, target_id) else None

    def replan(self, a: Agent) -> None:
        if a.going_to:
            self._go_person(a, a.going_to)
        elif a.target_exit:
            self.go(a, a.target_exit)
        elif a.target_stair and a.action in ("TAKE_STAIRS", "GO_TO_REFUGE"):
            self._go_stair(a, a.target_stair, a.action == "GO_TO_REFUGE")
        elif a.helping and not a.carrying:
            self._go_help(a, a.helping)

    def _replan_all(self) -> None:
        """Costs changed: moving agents keep walking and get a decision point (the decider re-routes)."""
        for a in self.active():
            if a.state in ("moving", "queued", "helping"):
                self.trigger(a, "route_changed", None)

    # ---- movement -------------------------------------------------------------
    def _move(self) -> None:
        buckets = [d.bucket for d in self.world.doors] + [e.bucket for e in self.world.exits.values()]
        for b in buckets + [s.bucket for s in self.world.stairs.values()]:
            b.refill()
        for a in self.active():
            if a.stair is not None:
                stairs.advance(self, a, DT)
        act = [a for a in self.active() if a.stair is None and a.state != "carried"]
        if act:
            self._walk(act)
        stairs.update_helpers(self)
        groups.update(self)
        self._exits([a for a in self.active() if a.state != "carried"])

    def _walk(self, act: list[Agent]) -> None:
        rows = np.array([a.row for a in act])
        pos, lv = self.pos[rows], self.lv[rows]
        self._skip_visible_waypoints(act, pos, lv)
        step = np.zeros_like(pos)
        walking = [a.state in ("moving", "queued", "helping") for a in act]
        for i, a in enumerate(act):
            a.group_waiting = walking[i] and groups.waits_for_group(self, a)
            if a.group_waiting:
                a.group_wait_s += DT
            elif walking[i]:
                step[i] = self._seek(a, pos[i])
        hurry = np.array([a.pace == "hurry" for a in act])
        new, denied, passed = resolve_moves(self.world, pos, lv, step, np.ones(len(act)), hurry)
        for r, door_id in passed:
            self.crossings.append((self.t, door_id, act[r].id))
            if act[r].action in ("EVACUATE", "FOLLOW"):
                self.trigger(act[r], "door_reached", door_id)
        moved = np.linalg.norm(new - pos, axis=1)
        self.pos[rows] = new
        for i, a in enumerate(act):
            if walking[i] and a.stair is None and not a.group_waiting:
                self._after_step(a, moved[i], float(np.linalg.norm(step[i])), bool(denied[i]))

    def _skip_visible_waypoints(self, act: list[Agent], pos: np.ndarray, lv: np.ndarray) -> None:
        """Drop the current waypoint when the one after it (same level, no stair) is in clear sight.

        Keeps agents that were pushed past a corner from walking back to it.
        """
        idx = [i for i, a in enumerate(act) if len(a.waypoints) >= 2 and a.waypoints[0].link is None
               and a.waypoints[1].link is None and a.waypoints[1].lv == lv[i]]
        if not idx:
            return
        ok = self.world.nav.clear_segments(pos[idx], np.array([act[i].waypoints[1].pt for i in idx]), lv[idx])
        for i, clear in zip(idx, ok):
            if clear:
                act[i].waypoints.pop(0)

    def _seek(self, a: Agent, p: np.ndarray) -> np.ndarray:
        """Step (m) toward the current waypoint, dropping waypoints already reached.

        A stair waypoint starts the stair traversal instead (zero step).
        """
        while a.waypoints and a.waypoints[0].link is None and np.linalg.norm(a.waypoints[0].pt - p) < self._reach_m(a):
            a.waypoints.pop(0)
        if not a.waypoints:
            if a.action == "GO_TO_REFUGE" and a.state != "waiting_at_refuge":
                stairs.refuge_reached(self, a)
            elif a.action == "TAKE_STAIRS":
                a.state = "waiting"
                self.trigger(a, "level_reached", self.world.nav.level_ids[int(self.lv[a.row])])
            return np.zeros(2)
        if a.hold:
            return np.zeros(2)
        if a.waypoints[0].link is not None:
            stairs.try_enter(self, a, a.waypoints[0].link)
            return np.zeros(2)
        d = a.waypoints[0].pt - p
        n = float(np.linalg.norm(d))
        tri = self.tri_of(a)
        speed = stairs.walk_speed(a) * (0.7 if self.world.nav.mult[tri] > 1 else 1.0)
        return d if n <= speed * DT else d / n * speed * DT

    def _reach_m(self, a: Agent) -> float:
        """How close counts as reached: exact for corners, looser for a stair entry or refuge (crowds)."""
        last = len(a.waypoints) == 1 and a.action == "GO_TO_REFUGE"
        before_stair = len(a.waypoints) >= 2 and a.waypoints[1].link is not None
        return STAIR_ENTRY_M if (last or before_stair) else WAYPOINT_EPS_M

    def _after_step(self, a: Agent, moved: float, wanted: float, denied: bool) -> None:
        if a.state in ("waiting", "waiting_at_refuge"):
            return
        a.state = "queued" if denied or (wanted > 0 and moved < 0.3 * wanted) else (
            "helping" if a.carrying else "moving")
        a.still_ticks = a.still_ticks + 1 if moved < 0.02 and not denied and a.waypoints else 0
        if a.still_ticks >= STUCK_TICKS and self.tick - a.replan_tick >= STUCK_TICKS:
            a.replan_tick, a.still_ticks = self.tick, 0
            self.replan(a)

    def _exits(self, act: list[Agent]) -> None:
        for a in sorted(act, key=lambda a: a.row):
            if a.state not in ("moving", "queued", "helping") or a.stair is not None:
                continue
            p = self.pos[a.row]
            for eid, ex in self.world.exits.items():
                if ex.blocked or ex.lv != self.lv[a.row] or np.linalg.norm(p - ex.goal) > EXIT_RADIUS_M:
                    continue
                if ex.bucket.take():
                    self._exit(a, eid)
                    if a.carrying and a.helping:
                        self._exit(next(b for b in self.agents if b.id == a.helping), eid)
                else:
                    a.state = "queued"
                break

    def _exit(self, a: Agent, eid: str) -> None:
        a.state, a.exit_t, a.exit_id, a.waypoints = "exited", self.t, eid, []
        self.crossings.append((self.t, eid, a.id))
        self.event("exited", agent=a.id, ref=eid, text=f"left by {self.world.exits[eid].label}")

    def _frame(self) -> None:
        rows = []
        for a in self.agents:
            x, z = self.pos[a.row]
            state = "deciding" if a.in_flight and a.state == "waiting" else a.state
            state = "waiting" if a.group_waiting and state != "exited" else state
            rows.append([a.id, round(float(x), 2), round(float(z), 2), state, self.world.nav.level_ids[self.lv[a.row]]])
        self.frames.append({"t": self.t, "a": rows})


_VIEW_KEYS = ("record_id", "run_id", "tick", "t_s", "agent_id", "trigger", "percepts", "beliefs_before", "options",
              "choice", "say", "apply_tick")


def _ids(x: object) -> list[str]:
    return [x] if isinstance(x, str) else list(x or [])


def _label(opt: dict) -> str:
    if opt.get("stair") and not opt.get("exit"):
        return f"{opt['action']} {opt['stair']}"
    if opt.get("target") and not opt.get("exit"):
        return f"{opt['action']} {opt['target']}"
    if opt.get("exit"):
        return f"{opt['action']} {opt['exit']}" + (f" (following {opt['target']})" if opt.get("target") else "")
    return opt["action"]


def _fallback(view: dict, errors: list[str]) -> dict:
    res = default_decide(view)
    res["record"]["validation"] = {"status": "fallback", "errors": errors}
    return res

