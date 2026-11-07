"""Occupant state and spawning from scenario occupants (deterministic by seed).

Times in s, positions [x, z] in m.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from sim.personas import PERSONAS, resolve, sample_traits
from sim.world import World

log = logging.getLogger("sim")


@dataclass
class Agent:
    """Mutable per-occupant sim state (engine internal; the decider only sees views)."""

    id: str
    row: int
    persona: str
    model: str
    traits: dict
    rng: np.random.Generator
    spawn_room: str | None
    entry_exit: str
    known_exits: list[str]
    state: str = "waiting"
    action: str = "CONTINUE_TASK"
    target_exit: str | None = None
    target_stair: str | None = None  # TAKE_STAIRS / GO_TO_REFUGE target
    waypoints: list = field(default_factory=list)  # sim.navmesh.Waypoint
    stair: dict | None = None  # in-progress stair traversal
    helper: str | None = None  # id of the agent carrying this one
    helping: str | None = None  # id of the agent this one is going to help or carrying
    carrying: bool = False
    pace: str = "brisk"
    hold: bool = False  # stopped until the next decision applies (landing, smoky or crowded stair)
    stair_asked: list = field(default_factory=list)  # (stair id, level) already asked about
    last_speech_trigger_t: float = -1e9
    cue_t: float | None = None
    first_move_t: float | None = None
    exit_t: float | None = None
    exit_id: str | None = None
    next_periodic_t: float = 1e18
    premove_fired: bool = False
    beliefs: dict[str, dict] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    hazards_seen: dict[str, dict] = field(default_factory=dict)
    signs_checked: list[str] = field(default_factory=list)
    trigger: dict | None = None
    trigger_prio: int = 0
    in_flight: bool = False
    last_decide_tick: int = -999
    last_say: tuple[str, float] | None = None
    still_ticks: int = 0
    replan_tick: int = -999


def _choice(rng: np.random.Generator, dist: dict[str, float]) -> str:
    keys = list(dist)
    w = np.asarray([max(float(dist[k]), 0.0) for k in keys])
    return keys[int(rng.choice(len(keys), p=w / w.sum()))] if w.sum() > 0 else keys[0]


def _exit_level(world: World) -> int:
    return next(iter(world.exits.values())).lv


def _entry_weights(world: World) -> dict[str, float]:
    """Entry exit prior: exits labeled main/entrance are three times as likely."""
    return {eid: 3.0 if any(k in ex.label.lower() for k in ("main", "entrance", "entry")) else 1.0
            for eid, ex in world.exits.items()}


def spawn(world: World, occupants: list[dict], seed: int,
          omniscient: bool = False) -> tuple[list[Agent], np.ndarray, np.ndarray]:
    """Create agents, start positions (N, 2) and level indices (N,) from scenario occupant groups."""
    root = np.random.SeedSequence(seed)
    place_rng = np.random.default_rng(root.spawn(1)[0])
    total = sum(int(g.get("count", 0)) for g in occupants)
    width = max(2, len(str(total)))
    agent_seqs = root.spawn(total + 1)[1:]
    agents: list[Agent] = []
    pos: list[np.ndarray] = []
    lvs: list[int] = []
    entry_w = _entry_weights(world)
    for g in occupants:
        where = g.get("where") or {}
        room = where.get("room")
        if room is not None and room not in world.room_polys:
            log.warning("occupants: unknown room %s, spawning anywhere walkable", room)
            room = None
        for _ in range(int(g.get("count", 0))):
            row = len(agents)
            rng = np.random.default_rng(agent_seqs[row])
            persona = resolve(_choice(rng, g.get("personas") or {"regular": 1.0}))
            model = _choice(rng, g.get("models") or {"rule-based": 1.0})
            lv = world.room_lv[room] if room else world.lv_index.get(where.get("level"), _exit_level(world))
            p = _spawn_point(world, room, lv, where, place_rng, [q for q, l in zip(pos, lvs) if l == lv])
            r = room or world.room_at(p, lv)
            tri = int(world.nav.locate(p[None], lv)[0])
            entry = g.get("familiar_exit") if g.get("familiar_exit") in world.exits else _choice(rng, entry_w)
            nearest = min(world.exits, key=lambda e: world.route_m(e, tri))
            if persona.knows_all or omniscient:
                known = list(world.exits)
            else:
                known = [entry] + ([nearest] if nearest != entry and rng.random() < persona.knows_nearest_p else [])
            traits = sample_traits(persona, rng)
            if omniscient:
                traits.update(premove_s=0.0, notice_s=0.0)
            agents.append(Agent(f"a_{row + 1:0{width}d}", row, persona.name, model, traits, rng, r, entry, known))
            pos.append(p)
            lvs.append(lv)
    return agents, np.asarray(pos, dtype=float).reshape(-1, 2), np.asarray(lvs, dtype=int)


def _spawn_point(world: World, room: str | None, lv: int, where: dict, rng: np.random.Generator,
                 taken: list[np.ndarray], tries: int = 20) -> np.ndarray:
    """Random walkable point in the room (or near `where.point`), avoiding overlap when possible."""
    best = None
    for _ in range(tries):
        if room is not None:
            p = world.room_point(room, rng)
        elif "point" in where:
            p = world.nav.snap(np.asarray(where["point"], float) + rng.normal(0, 1.0, 2), lv)
        else:
            x0, z0, x1, z1 = world.nav.walk[lv].bounds
            p = rng.uniform([x0, z0], [x1, z1])
            if not world.nav.is_walkable(p[None], lv)[0]:
                continue
        best = p
        if not taken or np.min(np.linalg.norm(np.asarray(taken) - p, axis=1)) >= 0.5:
            return p
    return best if best is not None else world.nav.snap(world.nav.centroids[world.nav.lv_tris[lv][0]], lv)


PERSONA_COLORS = {name: p.color for name, p in PERSONAS.items()}
