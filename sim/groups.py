"""Affiliation (research/EVAC_PSYCHOLOGY.md, Sime 1985): groups move together, parents fetch
children, staff sweep their floor.

Groups come from the scenario (occupants[].group = "family" | "coworkers", optional group_size)
or are formed by the sim: every parent gets a child (occupants[].children = false to opt out,
or {"room": id} to place the child elsewhere so the parent has to fetch it). Distances in m,
times in s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sim.agents import Agent
    from sim.engine import Sim

GROUP_SPREAD_M = 4.0  # a member this far behind makes the ones ahead wait
GROUP_WAIT_S = 30.0  # total time one member waits for the group before going on
FETCH_M = 3.0  # a parent further than this from its child goes to get it first
ARRIVE_FETCH_M = 1.5
ARRIVE_SWEEP_M = 3.0
REPATH_TICKS = 10
SWEEP_TEXT = "Fire alarm, leave the building now!"


def members(sim: "Sim", a: "Agent") -> list["Agent"]:
    return [b for b in sim.groups.get(a.group, []) if b is not a and b.state != "exited"] if a.group else []


def waits_for_group(sim: "Sim", a: "Agent") -> bool:
    """True when this mover should stop for a member who is behind (wait budget permitting)."""
    if not a.group or a.carrying or a.stair is not None or a.group_wait_s >= GROUP_WAIT_S or not a.target_exit:
        return False
    tri = sim.tri_of(a)
    mine = sim.world.route_cost(a.target_exit, tri)
    for b in members(sim, a):
        if b.state == "carried" or b.stair is not None:
            continue
        far = sim.lv[b.row] != sim.lv[a.row] or np.linalg.norm(sim.pos[b.row] - sim.pos[a.row]) > GROUP_SPREAD_M
        behind = sim.world.route_cost(a.target_exit, sim.tri_of(b)) > mine + 1.0 or b.state not in ("moving", "queued")
        if far and behind:
            return True
    return False


def on_departure(sim: "Sim", a: "Agent") -> None:
    """Someone in the group started to leave: the others get a decision point (and a cue)."""
    for b in members(sim, a):
        if b.cue_t is None:
            b.cue_t = sim.t
        if b.state in ("waiting", "deciding"):
            sim.trigger(b, "group_moving", a.id)


def fetch_target(sim: "Sim", a: "Agent") -> "Agent | None":
    """A parent's child that is not with them."""
    if a.persona != "parent":
        return None
    for b in members(sim, a):
        if b.persona == "child" and (sim.lv[b.row] != sim.lv[a.row]
                                     or np.linalg.norm(sim.pos[b.row] - sim.pos[a.row]) > FETCH_M):
            return b
    return None


def sweep_target(sim: "Sim", a: "Agent") -> "Agent | None":
    """For a sweeping warden: the nearest person on this floor who has not started to leave."""
    if a.persona != "staff" or not (sim.assisted or a.warden) or a.helping:
        return None
    lv = sim.lv[a.row]
    cand = [b for b in sim.active() if b is not a and sim.lv[b.row] == lv and b.persona != "staff" and not b.swept
            and b.state in ("waiting", "deciding") and b.first_move_t is None and not b.helper]
    return min(cand, key=lambda b: (float(np.linalg.norm(sim.pos[b.row] - sim.pos[a.row])), b.row), default=None)


def update(sim: "Sim") -> None:
    """Arrivals for GO_TO_PERSON (fetch) and SWEEP (instruct), and periodic re-paths toward moving targets."""
    by_id = {b.id: b for b in sim.agents}
    for a in sim.active():
        if not a.going_to:
            continue
        b = by_id.get(a.going_to)
        if b is None or b.state == "exited":
            a.going_to = None
            sim.trigger(a, "reached_person", None)
            continue
        reach = ARRIVE_SWEEP_M if a.action == "SWEEP" else ARRIVE_FETCH_M
        if sim.lv[a.row] == sim.lv[b.row] and np.linalg.norm(sim.pos[a.row] - sim.pos[b.row]) <= reach:
            _arrive(sim, a, b)
        elif sim.tick % REPATH_TICKS == 0 and a.stair is None:
            sim.replan(a)


def _arrive(sim: "Sim", a: "Agent", b: "Agent") -> None:
    a.going_to, a.waypoints, a.state, a.hold = None, [], "waiting", True
    if a.action == "SWEEP":
        b.swept = True
        sim.event("said", agent=a.id, ref=b.id, text=SWEEP_TEXT)
        sim.hear(b, a, SWEEP_TEXT, {}, urgent=True, source={"kind": "utterance", "from": a.id}, conf=0.9,
                 official=True)
        sim.trigger(b, "heard", a.id)
        sim.trigger(a, "sweep_next", b.id)
    else:
        sim.event("reached_person", agent=a.id, ref=b.id, text=f"{a.id} reached {b.id}")
        sim.trigger(a, "reached_person", b.id)
        sim.trigger(b, "group_moving", a.id)


def form(sim: "Sim", agents: list["Agent"]) -> None:
    """Register groups of newly spawned agents."""
    for a in agents:
        if a.group:
            sim.groups.setdefault(a.group, []).append(a)


def options(sim: "Sim", a: "Agent") -> list[dict]:
    """GO_TO_PERSON for a parent away from its child; SWEEP for a warden with people still sitting."""
    out = []
    for kind, target in (("GO_TO_PERSON", fetch_target(sim, a)), ("SWEEP", sweep_target(sim, a))):
        if target is None:
            continue
        corr = sim.world.nav.astar(sim.tri_of(a), sim.tri_of(target))
        if corr is not None:
            route = sum(sim.world.nav.walk_len(i, j) for i, j in zip(corr[:-1], corr[1:]))
            out.append({"action": kind, "target": target.id, "persona": target.persona,
                        "level": sim.world.nav.level_ids[sim.lv[target.row]], "route_m": round(route, 1)})
    return out


def percepts(sim: "Sim", a: "Agent") -> list[dict]:
    """Where the rest of my group is and what they do (people keep track of their companions)."""
    return [{"kind": "group_member", "ref": b.id, "persona": b.persona, "doing": b.action, "state": b.state,
             "coming_for_me": b.going_to == a.id,
             "toward": b.target_exit, "level": sim.world.nav.level_ids[sim.lv[b.row]],
             "dist_m": round(float(np.linalg.norm(sim.pos[b.row] - sim.pos[a.row])), 1), "channel": "affiliation"}
            for b in members(sim, a)]
