"""Elevator attempts and (optional) occupant-evacuation elevator rides.

In a fire the elevators are recalled (scene elevators[].usable_in_fire false). Some people still
walk to the elevator lobby first, find it out of service (percept elevator_recalled, state
at_elevator_lobby) and decide again. Assumption, not a published rate: the chance of trying is
ATTEMPT_P per persona below. `interventions: [{"type": "elevator_lobby_signage", "level": id}]`
(level optional = every floor) removes the attempt for people on that floor. With
`params.occupant_evacuation_elevators: true` elevators that serve an exit level carry up to
CAPACITY people every CYCLE_S to the lowest exit level. Times in s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sim.agents import Agent
    from sim.engine import Sim

ATTEMPT_P = {"older_adult": 0.3, "mobility_impaired": 0.5, "visitor": 0.1}
CAPACITY = 10
CYCLE_S = 60.0


def setup(sim: "Sim", scene: dict) -> None:
    """Elevator lobbies per level, service flag, and who will try the elevator first."""
    nav, lv_index = sim.world.nav, sim.world.lv_index
    params = sim.scenario.get("params") or {}
    sim.evac_elevators = bool(params.get("occupant_evacuation_elevators"))
    sim.elevators = {}
    for e in scene.get("elevators", []):
        lvs = [lv_index[x] for x in e.get("levels", []) if x in lv_index]
        exit_lvs = [lv for lv in lvs if lv in nav.exit_levels]
        if len(lvs) < 2:
            continue
        sim.elevators[e["id"]] = {
            "lobby": {lv: nav.snap(e["position"], lv) for lv in lvs},
            "ground": min(exit_lvs, key=lambda lv: nav.elev[lv]) if exit_lvs else None,
            "in_service": bool(e.get("usable_in_fire")) or (sim.evac_elevators and bool(exit_lvs)),
            "queue": [], "riding": [], "next_t": 0.0}
    assign(sim, sim.agents)


def assign(sim: "Sim", agents: list["Agent"]) -> None:
    """Who walks to the elevator lobby first (seeded per agent; signage on their floor stops it)."""
    no_attempt = _signage_levels(sim)
    for a in agents:
        a.traits["tries_elevator"] = bool(
            sim.elevators and int(sim.lv[a.row]) not in no_attempt
            and a.rng.random() < ATTEMPT_P.get(a.persona, 0.0))


def _signage_levels(sim: "Sim") -> set[int]:
    out: set[int] = set()
    for it in sim.scenario.get("interventions", []):
        if it.get("type") == "elevator_lobby_signage":
            lv = sim.world.lv_index.get(it.get("level"))
            out |= set(range(len(sim.world.nav.level_ids))) if lv is None else {lv}
    return out


def options(sim: "Sim", a: "Agent") -> list[dict]:
    """USE_ELEVATOR from floors without an exit, until the person has found it out of service."""
    lv = int(sim.lv[a.row])
    if a.elevator_recalled_seen or lv in sim.world.nav.exit_levels:
        return []
    out = []
    for eid, el in sim.elevators.items():
        lobby = el["lobby"].get(lv)
        if lobby is None:
            continue
        path = sim.world.path_to_point(sim.pos[a.row].copy(), lv, lobby)
        if path is None:
            continue
        pts = [sim.pos[a.row]] + [w.pt for w in path]
        route = float(sum(np.linalg.norm(q - p) for p, q in zip(pts[:-1], pts[1:])))
        out.append({"action": "USE_ELEVATOR", "elevator": eid, "route_m": round(route, 1),
                    "in_service": True if sim.evac_elevators and el["in_service"] else None})
    return out


def percepts(sim: "Sim", a: "Agent") -> list[dict]:
    return [{"kind": "elevator_recalled", "ref": a.target_elevator, "channel": "sight"}] if a.elevator_recalled_seen else []


def go(sim: "Sim", a: "Agent", eid: str) -> None:
    lv = int(sim.lv[a.row])
    lobby = sim.elevators[eid]["lobby"].get(lv)
    a.target_elevator, a.target_exit, a.target_stair = eid, None, None
    sim._start(a, None if lobby is None else sim.world.path_to_point(sim.pos[a.row].copy(), lv, lobby), eid)


def arrive(sim: "Sim", a: "Agent") -> None:
    """At the lobby: join the queue if the elevator runs, otherwise find it recalled and decide again."""
    el = sim.elevators[a.target_elevator]
    a.state = "at_elevator_lobby"
    if el["in_service"]:
        if a not in el["queue"]:
            el["queue"].append(a)
            sim.event("elevator_queue", agent=a.id, ref=a.target_elevator, text="waiting for the elevator")
        return
    a.elevator_recalled_seen = True
    sim.event("elevator_recalled", agent=a.id, ref=a.target_elevator, text="elevator out of service (fire recall)")
    sim.trigger(a, "elevator_recalled", a.target_elevator)


def update(sim: "Sim") -> None:
    """Board up to CAPACITY people every CYCLE_S; they arrive half a cycle later at the exit level."""
    for eid, el in sim.elevators.items():
        if not el["in_service"] or el["ground"] is None:
            continue
        for a, t_arr in list(el["riding"]):
            if sim.t >= t_arr:
                el["riding"].remove((a, t_arr))
                sim.pos[a.row], sim.lv[a.row] = el["lobby"][el["ground"]], el["ground"]
                a.state, a.hold, a.waypoints = "waiting", False, []
                sim.event("elevator_ride", agent=a.id, ref=eid, text="arrived by elevator")
                sim.trigger(a, "level_reached", sim.world.nav.level_ids[el["ground"]])
        el["queue"] = [a for a in el["queue"] if a.state == "at_elevator_lobby"]
        if el["queue"] and sim.t >= el["next_t"]:
            board, el["queue"] = el["queue"][:CAPACITY], el["queue"][CAPACITY:]
            el["riding"] += [(a, sim.t + CYCLE_S / 2) for a in board]
            el["next_t"] = sim.t + CYCLE_S
