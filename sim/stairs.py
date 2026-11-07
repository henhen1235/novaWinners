"""Stair traversal, areas of refuge and carrying (helper + mobility-impaired occupant).

Speeds in m/s along the stair (persona traits), stair flow capped by the
stair's bucket (1.1 persons/s per meter of width). Carrying caps the helper
(sim.pace). The sim never picks a route: at landings, smoky or crowded stairs,
blockages and pick-ups it stops the agent and asks the decider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from sim.navmesh import Link
from sim.pace import pace_speed, stair_pace_speed
from sim.perception import stair_status

if TYPE_CHECKING:
    from sim.agents import Agent
    from sim.engine import Sim

PICKUP_M = 1.0
CROWDED_QUEUE_S = 20.0


def try_enter(sim: "Sim", a: "Agent", link: Link) -> bool:
    """Start down/up a stair if it is open and has flow capacity this tick."""
    st = sim.world.stairs.get(link.id)
    if st is None or st.blocked:
        a.waypoints, a.state = [], "waiting"
        sim.trigger(a, "path_blocked", link.id)
        return False
    key = [link.id, int(link.a_lv)]
    stat = stair_status(sim, link.id, link.a_lv)
    if key not in a.stair_asked and (stat["smoke"] or stat["queue_s"] > CROWDED_QUEUE_S):
        a.stair_asked.append(key)  # the decider chooses whether to go in anyway
        a.hold, a.state = True, "queued"
        sim.trigger(a, "stair_smoke" if stat["smoke"] else "stair_crowded", link.id)
        return False
    if not st.bucket.take():
        a.state = "queued"
        return False
    a.stair = {"link": link, "prog": 0.0}
    sim.crossings.append((sim.t, link.id, a.id))
    return True


def advance(sim: "Sim", a: "Agent", dt: float) -> None:
    """Move an agent along its stair; on arrival it is on the far level with the link waypoint done."""
    link: Link = a.stair["link"]
    a.stair["prog"] += stair_pace_speed(a, link.down) * dt
    frac = min(1.0, a.stair["prog"] / max(link.length_m, 1e-6))
    sim.pos[a.row] = link.a_pt + (link.b_pt - link.a_pt) * frac
    sim.lv[a.row] = link.a_lv if frac < 0.5 else link.b_lv
    a.state = "helping" if a.carrying else "moving"
    if frac >= 1.0:
        a.stair = None
        if a.waypoints and a.waypoints[0].link is not None:
            a.waypoints.pop(0)
        a.hold = True  # landing: keep going, or leave the stairwell here? The decider chooses.
        sim.trigger(a, "level_reached", sim.world.nav.level_ids[link.b_lv])


def refuge_reached(sim: "Sim", a: "Agent") -> None:
    a.state = "waiting_at_refuge"
    sim.event("waiting_at_refuge", agent=a.id, ref=a.target_stair, text="waiting at the stair landing for help")
    for s in sim.active():  # radio call to wardens still in the building
        if s.persona == "staff" and not s.helping and s.stair is None:
            s.cue_t = sim.t if s.cue_t is None else s.cue_t
            sim.trigger(s, "help_request", a.id)


def update_helpers(sim: "Sim") -> None:
    """Pick up people waiting for help when their helper arrives; carried people follow helpers."""
    by_id = {a.id: a for a in sim.agents}
    for h in sim.agents:
        if h.state == "exited" or not h.helping:
            continue
        p = by_id.get(h.helping)
        if p is None or p.state == "exited":
            h.helping, h.carrying = None, False
            continue
        if not h.carrying and sim.lv[h.row] == sim.lv[p.row] and \
                np.linalg.norm(sim.pos[h.row] - sim.pos[p.row]) <= PICKUP_M and h.stair is None:
            _pick_up(sim, h, p)
        if h.carrying:
            sim.pos[p.row], sim.lv[p.row] = sim.pos[h.row], sim.lv[h.row]


def _pick_up(sim: "Sim", h: "Agent", p: "Agent") -> None:
    h.carrying = True
    p.state, p.helper, p.waypoints, p.stair = "carried", h.id, [], None
    if p.first_move_t is None:
        p.first_move_t = sim.t
    sim.event("helping", agent=h.id, ref=p.id, text=f"{h.id} is carrying {p.id}")
    h.waypoints, h.hold = [], True
    sim.trigger(h, "picked_up", p.id)


def walk_speed(a: "Agent") -> float:
    return pace_speed(a)
