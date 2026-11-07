"""What occupants sense (hazards, exits, people, speech) and the decide() view.

Distances in m, times in s. Sight is a straight line that stays on walkable.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sim.agents import Agent
    from sim.engine import Sim

SIGHT_M = {"fire": 15.0, "smoke": 10.0}
EXIT_CHECK_M = 3.0  # close enough to see whether an exit is open
EXIT_SIGN_M = 8.0
PEOPLE_M = 6.0
HEARING_M = 5.0
QUEUE_ZONE_M = 5.0
MESSAGE_MEMORY_S = 60.0


def sense(sim: "Sim") -> None:
    """Hazard sightings and exit checks for this tick; raises decision triggers."""
    act = sim.active()
    if not act:
        return
    if sim.tick % 2 == 0:
        _sense_hazards(sim, act)
    _sense_exits(sim, act)


def _sense_hazards(sim: "Sim", act: list["Agent"]) -> None:
    for ev in sim.events:
        if ev.type not in SIGHT_M or ev.phase == 0 or ev.point is None:
            continue
        cand = [a for a in act if ev.id not in a.hazards_seen]
        if not cand:
            continue
        pts = sim.pos[[a.row for a in cand]]
        dist = np.linalg.norm(pts - ev.point, axis=1)
        near = dist <= SIGHT_M[ev.type]
        seen = np.zeros(len(cand), dtype=bool)
        if near.any():
            seen[near] = sim.world.nav.line_of_sight(ev.point, pts[near])
        inside = np.isin(sim.world.nav.locate(pts), ev.tris)
        for a, d, s, ins in zip(cand, dist, seen, inside):
            if s or ins:
                a.hazards_seen[ev.id] = {"kind": ev.type, "where": ev.room, "ref": ev.id}
                if a.cue_t is None:
                    a.cue_t = sim.t
                sim.trigger(a, "hazard_seen", ev.id)


def _sense_exits(sim: "Sim", act: list["Agent"]) -> None:
    pts = sim.pos[[a.row for a in act]]
    for eid, ex in sim.world.exits.items():
        rows = np.flatnonzero(np.linalg.norm(pts - ex.goal, axis=1) <= EXIT_CHECK_M)
        if len(rows) == 0:
            continue
        vis = sim.world.nav.line_of_sight(ex.goal, pts[rows])
        for r in rows[vis]:
            a = act[r]
            if eid not in a.known_exits:
                a.known_exits.append(eid)
            changed = sim.believe(a, f"exit_open:{eid}", not ex.blocked, 1.0, {"kind": "observation", "ref": eid})
            if changed and ex.blocked:
                sim.event("blocked", agent=a.id, ref=eid, text=f"found {ex.label} blocked")
                sim.learn_nearest_exit(a)
                if a.target_exit == eid or a.cue_t is not None:
                    sim.trigger(a, "path_blocked", eid)


def _neighbors(sim: "Sim", a: "Agent", radius: float) -> list["Agent"]:
    others = [b for b in sim.active() if b is not a]
    if not others:
        return []
    pts = sim.pos[[b.row for b in others]]
    rows = np.flatnonzero(np.linalg.norm(pts - sim.pos[a.row], axis=1) <= radius)
    if len(rows) == 0:
        return []
    vis = sim.world.nav.line_of_sight(sim.pos[a.row], pts[rows])
    return [others[r] for r in rows[vis]]


def _queue_s(sim: "Sim", eid: str) -> float:
    """Rough wait at an exit: people within QUEUE_ZONE_M of it divided by its flow (s)."""
    cache = getattr(sim, "_queue_cache", None)
    if cache is None or cache[0] != sim.tick:
        act = sim.active()
        pts = sim.pos[[a.row for a in act]] if act else np.zeros((0, 2))
        counts = {e: int(np.sum(np.linalg.norm(pts - ex.goal, axis=1) <= QUEUE_ZONE_M)) if len(pts) else 0
                  for e, ex in sim.world.exits.items()}
        sim._queue_cache = cache = (sim.tick, counts)
    return round(cache[1][eid] / sim.world.exits[eid].bucket.cap, 1)


def _percepts(sim: "Sim", a: "Agent", movers: list["Agent"]) -> list[dict]:
    p = sim.pos[a.row]
    out: list[dict] = []
    if sim.t >= sim.alarm_t:
        out.append({"kind": "alarm", "channel": "sound", "t_since_s": round(sim.t - sim.alarm_t, 1)})
    for ev in sim.events:
        if ev.id in a.hazards_seen and ev.point is not None:
            out.append({**a.hazards_seen[ev.id], "dist_m": round(float(np.linalg.norm(p - ev.point)), 1),
                        "channel": "sight"})
    if movers:
        toward = Counter(b.target_exit for b in movers if b.target_exit).most_common(1)
        out.append({"kind": "people_moving", "count": len(movers), "toward": toward[0][0] if toward else None,
                    "channel": "sight"})
    for m in a.messages[-5:]:
        if sim.t - m["t"] <= MESSAGE_MEMORY_S:
            out.append({"kind": "message", "from": m["from"], "text": m["text"], "urgent": m["urgent"],
                        "channel": "hearing", "ref": m["id"]})
    out += _exit_signs(sim, a)
    return [{"id": f"p{i + 1}", **d} for i, d in enumerate(out)]


def _exit_signs(sim: "Sim", a: "Agent") -> list[dict]:
    """Visible exits nearby; an unknown one is learned with the persona's sign-following chance."""
    p = sim.pos[a.row]
    out = []
    for eid, ex in sim.world.exits.items():
        d = float(np.linalg.norm(p - ex.goal))
        if d > EXIT_SIGN_M or not sim.world.nav.line_of_sight(p, ex.goal[None])[0]:
            continue
        if eid not in a.known_exits and eid not in a.signs_checked:
            a.signs_checked.append(eid)
            if a.rng.random() < a.traits["sign_follow_p"]:
                a.known_exits.append(eid)
        out.append({"kind": "exit_sign", "ref": eid, "dist_m": round(d, 1), "channel": "sight"})
    return out


def _options(sim: "Sim", a: "Agent", tri: int, movers: list["Agent"]) -> list[dict]:
    exits = [e for e, ex in sim.world.exits.items() if not ex.blocked] if sim.omniscient else a.known_exits
    out: list[dict] = []
    for eid in exits:
        route = sim.world.route_m(eid, tri)
        if np.isfinite(route):
            cost = sim.world.route_cost(eid, tri)
            out.append({"action": "EVACUATE", "exit": eid, "label": sim.world.exits[eid].label,
                        "route_m": round(route, 1), "queue_s": _queue_s(sim, eid),
                        "through_hazard": bool(cost > route * 1.05 + 0.5)})
    if not sim.omniscient:
        lead = min((b for b in movers if b.target_exit), default=None,
                   key=lambda b: float(np.linalg.norm(sim.pos[b.row] - sim.pos[a.row])))
        if lead is not None and np.isfinite(sim.world.route_m(lead.target_exit, tri)):
            out.append({"action": "FOLLOW", "target": lead.id, "exit": lead.target_exit,
                        "route_m": round(sim.world.route_m(lead.target_exit, tri), 1)})
        out += [{"action": "SEEK_INFO"}, {"action": "CONTINUE_TASK"}]
    if not out:
        out.append({"action": "CONTINUE_TASK"})
    return [{"id": f"o{i + 1}", **o} for i, o in enumerate(out)]


def build_view(sim: "Sim", a: "Agent") -> dict:
    """The decide() input for one agent (docs/SIM_INTERFACES.md)."""
    p = sim.pos[a.row]
    tri = int(sim.world.nav.locate(p[None])[0])
    movers = [b for b in _neighbors(sim, a, PEOPLE_M) if b.state in ("moving", "queued")]
    percepts = _percepts(sim, a, movers)
    traits = {**a.traits, "t_since_cue_s": round(sim.t - a.cue_t, 1) if a.cue_t is not None else None,
              "activity": a.action, "state": a.state}
    return {
        "run_id": sim.run_id, "t_s": sim.t, "tick": sim.tick, "trigger": dict(a.trigger or {"kind": "periodic"}),
        "agent": {"id": a.id, "persona": a.persona, "model": a.model,
                  "pos": [round(float(p[0]), 2), round(float(p[1]), 2)], "room": sim.world.room_at(p),
                  "known_exits": list(a.known_exits), "entry_exit": a.entry_exit, "target_exit": a.target_exit,
                  "traits": traits},
        "percepts": percepts,
        "beliefs": [dict(b) for b in a.beliefs.values()],
        "options": _options(sim, a, tri, movers),
    }


def broadcast(sim: "Sim", a: "Agent", text: str, urgent: bool) -> None:
    """Speech reaches agents within HEARING_M in sight; it carries the speaker's blocked-exit beliefs."""
    claims = [b["key"].split(":", 1)[1] for b in a.beliefs.values()
              if b["key"].startswith("exit_open:") and b["value"] is False and b["conf"] >= 0.5]
    conf = 0.8 * max((b["conf"] for b in a.beliefs.values() if b["key"].split(":", 1)[-1] in claims), default=0.0)
    for b in _neighbors(sim, a, HEARING_M):
        sim.hear(b, a, text, {"exit_blocked": claims} if claims else {}, urgent,
                 {"kind": "utterance", "from": a.id}, conf)
        sim.trigger(b, "heard", a.id)
