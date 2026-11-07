"""What occupants sense (hazards, exits, stairs, people, speech) and the decide() view.

Distances in m, times in s, speeds in m/s. Sight is a straight line that stays
on the walkable area of the viewer's own level.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import numpy as np

from sim.pace import PACES, pace_speed

if TYPE_CHECKING:
    from sim.agents import Agent
    from sim.engine import Sim

SIGHT_M = {"fire": 15.0, "smoke": 10.0}
EXIT_CHECK_M = 3.0  # close enough to see whether an exit is open
EXIT_SIGN_M = 8.0
STAIR_SIGHT_M = 10.0
PEOPLE_M = 6.0
MAX_PERSON_PERCEPTS = 8
HEARING_M = 5.0
QUEUE_ZONE_M = 5.0
MESSAGE_MEMORY_S = 60.0
SPEECH_TRIGGER_COOLDOWN_S = 8.0


def sense(sim: "Sim") -> None:
    """Hazard sightings and exit checks for this tick; raises decision triggers."""
    act = [a for a in sim.active() if a.stair is None]
    if not act:
        return
    if sim.tick % 2 == 0:
        _sense_hazards(sim, act)
    _sense_exits(sim, act)


def _rows(sim: "Sim", agents: list["Agent"]) -> tuple[np.ndarray, np.ndarray]:
    idx = [a.row for a in agents]
    return sim.pos[idx], sim.lv[idx]


def _sense_hazards(sim: "Sim", act: list["Agent"]) -> None:
    for ev in sim.events:
        if ev.type not in SIGHT_M or ev.phase == 0 or ev.point is None:
            continue
        cand = [a for a in act if ev.id not in a.hazards_seen and sim.lv[a.row] == ev.lv]
        if not cand:
            continue
        pts, _ = _rows(sim, cand)
        near = np.linalg.norm(pts - ev.point, axis=1) <= SIGHT_M[ev.type]
        seen = np.zeros(len(cand), dtype=bool)
        if near.any():
            seen[near] = sim.world.nav.line_of_sight(ev.point, pts[near], ev.lv)
        inside = np.isin(sim.world.nav.locate(pts, ev.lv), ev.tris)
        for a, s, ins in zip(cand, seen, inside):
            if s or ins:
                a.hazards_seen[ev.id] = {"kind": ev.type, "where": ev.room, "ref": ev.id,
                                         "level": sim.world.nav.level_ids[ev.lv]}
                if a.cue_t is None:
                    a.cue_t = sim.t
                sim.trigger(a, "hazard_seen", ev.id)


def _sense_exits(sim: "Sim", act: list["Agent"]) -> None:
    pts, lvs = _rows(sim, act)
    for eid, ex in sim.world.exits.items():
        rows = np.flatnonzero((np.linalg.norm(pts - ex.goal, axis=1) <= EXIT_CHECK_M) & (lvs == ex.lv))
        if len(rows) == 0:
            continue
        vis = sim.world.nav.line_of_sight(ex.goal, pts[rows], ex.lv)
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


def neighbors(sim: "Sim", a: "Agent", radius: float) -> list["Agent"]:
    """Agents on the same level within `radius` and in line of sight (not on a stair)."""
    lv = sim.lv[a.row]
    others = [b for b in sim.active() if b is not a and sim.lv[b.row] == lv and b.stair is None]
    if not others:
        return []
    pts, _ = _rows(sim, others)
    rows = np.flatnonzero(np.linalg.norm(pts - sim.pos[a.row], axis=1) <= radius)
    if len(rows) == 0:
        return []
    vis = sim.world.nav.line_of_sight(sim.pos[a.row], pts[rows], int(lv))
    return [others[r] for r in rows[vis]]


def _queue_s(sim: "Sim", eid: str) -> float:
    """Rough wait at an exit: people within QUEUE_ZONE_M of it divided by its flow (s)."""
    cache = getattr(sim, "_queue_cache", None)
    if cache is None or cache[0] != sim.tick:
        act = sim.active()
        pts, lvs = _rows(sim, act) if act else (np.zeros((0, 2)), np.zeros(0, dtype=int))
        counts = {e: int(np.sum((np.linalg.norm(pts - ex.goal, axis=1) <= QUEUE_ZONE_M) & (lvs == ex.lv)))
                  if len(pts) else 0 for e, ex in sim.world.exits.items()}
        sim._queue_cache = cache = (sim.tick, counts)
    return round(cache[1][eid] / sim.world.exits[eid].bucket.cap, 1)


def stair_load(sim: "Sim", sid: str, lv: int) -> tuple[int, int]:
    """(people on the stair, people waiting within QUEUE_ZONE_M of its entry on level lv)."""
    on = sum(1 for a in sim.active() if a.stair is not None and a.stair["link"].id == sid)
    link = sim.world.stair_link(sid, lv)
    if link is None:
        return on, 0
    near = sum(1 for a in sim.active() if a.stair is None and sim.lv[a.row] == lv
               and np.linalg.norm(sim.pos[a.row] - link.a_pt) <= QUEUE_ZONE_M)
    return on, near


def _percepts(sim: "Sim", a: "Agent", seen: list["Agent"]) -> list[dict]:
    p, lv = sim.pos[a.row], int(sim.lv[a.row])
    out: list[dict] = []
    if sim.t >= sim.alarm_t:
        out.append({"kind": "alarm", "channel": "sound", "t_since_s": round(sim.t - sim.alarm_t, 1)})
    for ev in sim.events:
        if ev.id in a.hazards_seen and ev.point is not None:
            d = float(np.linalg.norm(p - ev.point)) if ev.lv == lv else None
            out.append({**a.hazards_seen[ev.id], "dist_m": round(d, 1) if d is not None else None, "channel": "sight"})
    movers = [b for b in seen if b.state in ("moving", "queued", "helping")]
    if movers:
        toward = Counter(b.target_exit for b in movers if b.target_exit).most_common(1)
        out.append({"kind": "people_moving", "count": len(movers), "toward": toward[0][0] if toward else None,
                    "channel": "sight"})
    out += _person_percepts(sim, a, seen)
    for m in a.messages[-5:]:
        if sim.t - m["t"] <= MESSAGE_MEMORY_S:
            out.append({"kind": "message", "from": m["from"], "text": m["text"], "urgent": m["urgent"],
                        "official": m.get("official", False), "channel": "hearing", "ref": m["id"]})
    out += _exit_signs(sim, a) + _stair_percepts(sim, a)
    if a.persona == "staff":
        out += [{"kind": "needs_help", "ref": b.id, "level": sim.world.nav.level_ids[sim.lv[b.row]],
                 "channel": "radio"} for b in _waiting_for_help(sim)]
    return [{"id": f"p{i + 1}", **d} for i, d in enumerate(out)]


def _person_percepts(sim: "Sim", a: "Agent", seen: list["Agent"]) -> list[dict]:
    """The nearest people in sight and what they appear to be doing (for sight-based contagion)."""
    p = sim.pos[a.row]
    near = sorted(seen, key=lambda b: (float(np.linalg.norm(sim.pos[b.row] - p)), b.row))[:MAX_PERSON_PERCEPTS]
    return [{"kind": "person", "ref": b.id, "persona": b.persona, "doing": b.action, "state": b.state,
             "toward": b.target_exit, "dist_m": round(float(np.linalg.norm(sim.pos[b.row] - p)), 1),
             "channel": "sight"} for b in near]


def _exit_signs(sim: "Sim", a: "Agent") -> list[dict]:
    """Visible exits nearby; an unknown one is learned with the persona's sign-following chance."""
    p, lv = sim.pos[a.row], int(sim.lv[a.row])
    out = []
    for eid, ex in sim.world.exits.items():
        if ex.lv != lv:
            continue
        d = float(np.linalg.norm(p - ex.goal))
        if d > EXIT_SIGN_M or not sim.world.nav.line_of_sight(p, ex.goal[None], lv)[0]:
            continue
        if eid not in a.known_exits and eid not in a.signs_checked:
            a.signs_checked.append(eid)
            if a.rng.random() < a.traits["sign_follow_p"]:
                a.known_exits.append(eid)
        out.append({"kind": "exit_sign", "ref": eid, "dist_m": round(d, 1), "channel": "sight"})
    return out


def stair_status(sim: "Sim", sid: str, lv: int) -> dict:
    """Smoke, blockage and crowding of one stair as seen from level lv."""
    on, near = stair_load(sim, sid, lv)
    st = sim.world.stairs[sid]
    mult = sim.world.nav.stair_mult.get(sid, 1.0)
    return {"on_stairs": on, "waiting": near, "smoke": bool(mult > 1.0), "blocked": bool(st.blocked or np.isinf(mult)),
            "queue_s": round((on + near) / st.bucket.cap, 1)}


def _stair_percepts(sim: "Sim", a: "Agent") -> list[dict]:
    """Stairs in sight on this level: crowding, smoke in the stairwell, where they lead."""
    p, lv = sim.pos[a.row], int(sim.lv[a.row])
    out = []
    for sid in sim.world.stairs:
        link = sim.world.stair_link(sid, lv)
        if link is None:
            continue
        d = float(np.linalg.norm(p - link.a_pt))
        if d > STAIR_SIGHT_M or not sim.world.nav.line_of_sight(p, link.a_pt[None], lv)[0]:
            continue
        stat = stair_status(sim, sid, lv)
        out.append({"kind": "stair", "ref": sid, "dist_m": round(d, 1), "direction": "down" if link.down else "up",
                    "leads_to_level": sim.world.nav.level_ids[link.b_lv], "channel": "sight",
                    **{k: v for k, v in stat.items() if k != "queue_s"}})
    return out


def _waiting_for_help(sim: "Sim") -> list["Agent"]:
    return [b for b in sim.active() if b.state == "waiting_at_refuge" and not b.helper]


def _eta(sim: "Sim", a: "Agent", route_m: float, queue_s: float, flights: int) -> float:
    """Seconds to get there at this agent's brisk pace, with stair flights at stair speed."""
    v = pace_speed(a, "brisk")
    stair_len = 2.0 * 3.5  # ponytail: typical flight length; per-link lengths if this matters
    stair_extra = flights * stair_len * (1.0 / max(a.traits["stair_down_mps"], 0.1) - 1.0 / v)
    return round(route_m / v + queue_s + max(stair_extra, 0.0), 1)


def _exit_options(sim: "Sim", a: "Agent", tri: int) -> list[dict]:
    ok = bool(a.traits["uses_stairs"]) or a.carrying
    exits = [e for e, ex in sim.world.exits.items() if not ex.blocked] if sim.omniscient else a.known_exits
    lv = int(sim.lv[a.row])
    out = []
    for eid in exits:
        route = sim.world.route_m(eid, tri, ok)
        if not np.isfinite(route):
            continue
        q = _queue_s(sim, eid)
        flights = abs(sim.world.exits[eid].lv - lv)
        out.append({"action": "EVACUATE", "exit": eid, "label": sim.world.exits[eid].label,
                    "route_m": round(route, 1), "queue_s": q, "eta_s": _eta(sim, a, route, q, flights),
                    "through_hazard": sim.world.through_hazard(eid, tri, ok),
                    "stair": sim.world.route_stair(eid, tri) if ok else None})
    return out


def _stair_options(sim: "Sim", a: "Agent") -> list[dict]:
    """TAKE_STAIRS on floors without an exit (and on landings); GO_TO_REFUGE for people who cannot use stairs."""
    lv = int(sim.lv[a.row])
    on_exit_floor = any(ex.lv == lv for ex in sim.world.exits.values())
    if on_exit_floor and a.traits["uses_stairs"]:
        return []
    out = []
    for sid in sim.world.stairs:
        link = sim.world.stair_link(sid, lv)
        if link is None:
            continue
        walk = sim.world.route_to_point_m(sim.pos[a.row], lv, link.a_pt)
        if not np.isfinite(walk):
            continue
        if not a.traits["uses_stairs"] and not a.carrying:
            out.append({"action": "GO_TO_REFUGE", "stair": sid, "route_m": round(walk, 1),
                        "eta_s": round(walk / pace_speed(a, "brisk"), 1)})
            continue
        stat = stair_status(sim, sid, lv)
        out.append({"action": "TAKE_STAIRS", "stair": sid, "direction": "down" if link.down else "up",
                    "route_m": round(walk + link.length_m, 1), "queue_s": stat["queue_s"],
                    "eta_s": _eta(sim, a, walk, stat["queue_s"], 1),
                    "leads_to_level": sim.world.nav.level_ids[link.b_lv], "blocked": stat["blocked"],
                    "smoke": stat["smoke"]})
    return out


def _help_options(sim: "Sim", a: "Agent", tri: int) -> list[dict]:
    if a.persona != "staff" or a.helping:
        return []
    out = []
    for b in _waiting_for_help(sim):
        corr = sim.world.nav.astar(tri, sim.tri_of(b))
        if corr is not None:
            route = sum(sim.world.nav.walk_len(i, j) for i, j in zip(corr[:-1], corr[1:]))
            out.append({"action": "HELP", "target": b.id, "level": sim.world.nav.level_ids[sim.lv[b.row]],
                        "route_m": round(route, 1), "eta_s": round(route / pace_speed(a, "brisk"), 1)})
    return out


def _options(sim: "Sim", a: "Agent", tri: int, seen: list["Agent"]) -> list[dict]:
    out = _exit_options(sim, a, tri)
    if not sim.omniscient:
        out += _stair_options(sim, a) + _help_options(sim, a, tri)
        movers = [b for b in seen if b.state in ("moving", "queued") and b.target_exit]
        lead = min(movers, default=None, key=lambda b: float(np.linalg.norm(sim.pos[b.row] - sim.pos[a.row])))
        ok = a.traits["uses_stairs"]
        if lead is not None and np.isfinite(sim.world.route_m(lead.target_exit, tri, ok)):
            route = sim.world.route_m(lead.target_exit, tri, ok)
            out.append({"action": "FOLLOW", "target": lead.id, "exit": lead.target_exit, "route_m": round(route, 1),
                        "eta_s": round(route / pace_speed(a, "brisk"), 1)})
        out += [{"action": "SEEK_INFO"}, {"action": "CONTINUE_TASK"}]
    if not out:
        out.append({"action": "CONTINUE_TASK"})
    for o in out:
        if "route_m" in o:
            o["paces"] = list(PACES)
    return [{"id": f"o{i + 1}", **o} for i, o in enumerate(out)]


def build_view(sim: "Sim", a: "Agent") -> dict:
    """The decide() input for one agent (docs/SIM_INTERFACES.md)."""
    p, lv = sim.pos[a.row], int(sim.lv[a.row])
    tri = sim.tri_of(a)
    seen = neighbors(sim, a, PEOPLE_M)
    percepts = _percepts(sim, a, seen)
    traits = {**a.traits, "t_since_cue_s": round(sim.t - a.cue_t, 1) if a.cue_t is not None else None,
              "activity": a.action, "state": a.state, "pace": a.pace}
    return {
        "run_id": sim.run_id, "t_s": sim.t, "tick": sim.tick, "trigger": dict(a.trigger or {"kind": "periodic"}),
        "agent": {"id": a.id, "persona": a.persona, "model": a.model,
                  "pos": [round(float(p[0]), 2), round(float(p[1]), 2)], "level": sim.world.nav.level_ids[lv],
                  "room": sim.world.room_at(p, lv), "known_exits": list(a.known_exits), "entry_exit": a.entry_exit,
                  "target_exit": a.target_exit, "target_stair": a.target_stair,
                  "carrying": a.helping if a.carrying else None, "traits": traits},
        "percepts": percepts,
        "beliefs": [dict(b) for b in a.beliefs.values()],
        "options": _options(sim, a, tri, seen),
    }


def _is_news(sim: "Sim", listener: "Agent", speaker: "Agent", claims: list[str]) -> bool:
    """Speech triggers a decision only if it tells this listener something new."""
    if listener.cue_t is None or speaker.persona == "staff":
        return True
    return any(listener.beliefs.get(f"exit_open:{c}", {}).get("value") is not False for c in claims)


def broadcast(sim: "Sim", a: "Agent", text: str, urgent: bool) -> None:
    """Speech reaches agents within HEARING_M in sight; it carries the speaker's blocked-exit beliefs.

    Listeners always hear it (it shows in their percepts), but only news triggers a
    decision, at most once per SPEECH_TRIGGER_COOLDOWN_S per listener.
    """
    claims = [b["key"].split(":", 1)[1] for b in a.beliefs.values()
              if b["key"].startswith("exit_open:") and b["value"] is False and b["conf"] >= 0.5]
    conf = 0.8 * max((b["conf"] for b in a.beliefs.values() if b["key"].split(":", 1)[-1] in claims), default=0.0)
    for b in neighbors(sim, a, HEARING_M):
        news = _is_news(sim, b, a, claims)
        sim.hear(b, a, text, {"exit_blocked": claims} if claims else {}, urgent,
                 {"kind": "utterance", "from": a.id}, conf, official=a.persona == "staff")
        if news and sim.t - b.last_speech_trigger_t >= SPEECH_TRIGGER_COOLDOWN_S:
            b.last_speech_trigger_t = sim.t
            sim.trigger(b, "heard", a.id)
