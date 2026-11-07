"""Fill a building with exactly `total` people: populate(scene, 120, mode="load", preset="dorm_night").

Modes (where people go; all deterministic for a given seed):
- load: rooms in proportion to their IBC occupant load (analysis/occupancy.py), largest remainder.
  Circulation rooms (corridor, stairwell, restroom) have load 0, so nobody starts there.
- random: a seeded multinomial draw over rooms in proportion to their walkable area, so
  corridors and stairwells get people too.
- llm: the per-room proposals of scenario/seed.py, rescaled to the total; keeps the LLM's
  per-room persona mix (the preset then only adds its staff rule).
Presets set the persona mix; dorm_night also puts 2 resident-assistant staff on each floor
(counted inside the total). No room gets more than the 2 per m2 hard cap of scenario/schema.py;
overflow moves to rooms with space. A total above the summed design load is still placed, with
a note; a total above the summed hard cap raises ValueError.
"""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import Polygon

from analysis.occupancy import analyze as occupancy

from .schema import DEFAULT_MODEL, MAX_PER_M2, PERSONAS, validate

MODES = ("load", "random", "llm")
# Mixes are our assumptions [H] for demo scenarios, not survey data.
PRESETS: dict[str, dict[str, float]] = {
    "office_day": {"regular_employee": 0.72, "first_time_visitor": 0.12, "warden": 0.06, "older_adult": 0.06,
                   "mobility_impaired": 0.04},
    "dorm_night": {"regular_employee": 0.96, "first_time_visitor": 0.025, "mobility_impaired": 0.015},
    "event": {"first_time_visitor": 0.78, "regular_employee": 0.08, "warden": 0.04, "older_adult": 0.06,
              "parent_with_child": 0.03, "mobility_impaired": 0.01},
}
STAFF_PER_FLOOR = {"dorm_night": 2}  # resident assistants on duty per floor


def _rooms(scene: dict, levels: list[str] | None) -> list[dict]:
    known = {lv["id"] for lv in scene["levels"]}
    bad = [lv for lv in levels or [] if lv not in known]
    if bad:
        raise ValueError(f"unknown levels {bad}; known: {sorted(known)}")
    rooms = [r for r in scene["rooms"] if r["type"] != "outside" and (not levels or r["level"] in levels)]
    if not rooms:
        raise ValueError("no rooms on the chosen levels")
    return rooms


def _walkable_area(scene: dict, room: dict) -> float:
    polys = [Polygon(p["exterior"], p.get("holes", [])) for p in scene["walkable"].get(room["level"], {}).get("polygons", [])]
    return shapely.union_all(polys).intersection(Polygon(room["polygon"])).area if polys else 0.0


def _largest_remainder(total: int, weights: dict[str, float]) -> dict[str, int]:
    s = sum(weights.values())
    share = {k: total * w / s for k, w in weights.items()}
    counts = {k: int(v) for k, v in share.items()}
    for k in sorted(share, key=lambda k: (share[k] - counts[k], k), reverse=True)[:total - sum(counts.values())]:
        counts[k] += 1
    return counts


def _allocate(total: int, weights: dict[str, float], caps: dict[str, int], rng, random: bool) -> dict[str, int]:
    counts = dict.fromkeys(weights, 0)
    remaining = total
    while remaining > 0:
        open_ = {k: w for k, w in weights.items() if counts[k] < caps[k] and w > 0}
        if not open_:  # every weighted room is full: spill into any room with space, by cap
            open_ = {k: float(caps[k] - counts[k]) for k in weights if counts[k] < caps[k]}
        if random:
            keys = sorted(open_)
            p = np.asarray([open_[k] for k in keys])
            draw = dict(zip(keys, rng.multinomial(remaining, p / p.sum())))
        else:
            draw = _largest_remainder(remaining, open_)
        for k, n in draw.items():
            add = min(int(n), caps[k] - counts[k])
            counts[k] += add
            remaining -= add
    return counts


def _staff_groups(counts: dict[str, int], rooms: dict[str, dict], per_floor: int) -> tuple[dict[str, int], list[dict]]:
    """Take `per_floor` people per level out of its fullest rooms and make them staff."""
    counts, groups = dict(counts), []
    for lv in sorted({r["level"] for r in rooms.values()}):
        for _ in range(per_floor):
            pool = [k for k in counts if rooms[k]["level"] == lv and counts[k] > 0]
            if not pool:
                break
            k = max(pool, key=lambda k: (counts[k], k))
            counts[k] -= 1
            groups.append({"where": {"room": k}, "count": 1, "personas": {"warden": 1.0},
                           "models": {DEFAULT_MODEL: 1.0}})
    return counts, groups


def populate(scene: dict, total: int, mode: str = "load", preset: str = "office_day",
             personas: dict[str, float] | None = None, levels: list[str] | None = None, seed: int = 42,
             use_llm: bool = True) -> tuple[list[dict], list[str]]:
    """Occupant groups placing exactly `total` people, plus notes (assumptions) to show the user."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if preset != "custom" and preset not in PRESETS:
        raise ValueError(f"preset must be one of {sorted(PRESETS) + ['custom']}")
    if not isinstance(total, int) or not 1 <= total <= 20000:
        raise ValueError("total must be an integer 1..20000")
    mix = dict(personas or {}) if preset == "custom" or personas else dict(PRESETS[preset])
    if not mix or any(k not in PERSONAS for k in mix) or abs(sum(mix.values()) - 1) > 0.01:
        raise ValueError(f"personas must be weights summing to 1 over {list(PERSONAS)}")
    rooms = {r["id"]: r for r in _rooms(scene, levels)}
    caps = {k: int(MAX_PER_M2 * r["area_m2"]) for k, r in rooms.items()}
    if total > sum(caps.values()):
        raise ValueError(f"{total} people exceed the hard cap of {sum(caps.values())} ({MAX_PER_M2:g} per m2) "
                         "on the chosen levels")
    loads = {k: v["load"] for k, v in occupancy(scene)["rooms"].items() if k in rooms}
    notes = [f"populate: {total} people, mode {mode}, preset {preset}"
             + (f", levels {', '.join(levels)}" if levels else "") + f", seed {seed}"]
    if total > sum(loads.values()):
        notes.append(f"{total} people exceed the summed IBC design load of {sum(loads.values())} on these rooms")
    rng = np.random.default_rng(seed)
    llm_mix: dict[str, dict] = {}
    if mode == "load":
        weights = {k: float(v) for k, v in loads.items()}
        if not any(weights.values()):
            weights = {k: r["area_m2"] for k, r in rooms.items()}
            notes.append("no room has a design load (all circulation): weighted by area")
    elif mode == "random":
        weights = {k: _walkable_area(scene, r) for k, r in rooms.items()}
    else:
        from .seed import seed as llm_seed  # ponytail: lazy import keeps populate usable without LLM deps
        proposed, seed_notes = llm_seed({**scene, "rooms": list(rooms.values())}, use_llm=use_llm)
        notes += seed_notes
        weights = dict.fromkeys(rooms, 0.0) | {g["where"]["room"]: float(g["count"]) for g in proposed}
        llm_mix = {g["where"]["room"]: g["personas"] for g in proposed}
        if not any(weights.values()):
            weights = {k: float(v) for k, v in loads.items()}
    counts = _allocate(total, weights, caps, rng, random=mode == "random")
    staff: list[dict] = []
    if STAFF_PER_FLOOR.get(preset):
        counts, staff = _staff_groups(counts, rooms, STAFF_PER_FLOOR[preset])
        notes.append(f"{len(staff)} resident-assistant staff ({STAFF_PER_FLOOR[preset]} per floor) inside the total")
    groups = [{"where": {"room": k}, "count": n, "personas": llm_mix.get(k, mix), "models": {DEFAULT_MODEL: 1.0}}
              for k, n in sorted(counts.items()) if n > 0] + staff
    errors = validate({"scene": "", "occupants": groups}, scene)
    if errors:  # should not happen: caps are respected above
        raise ValueError("; ".join(errors[:3]))
    assert sum(g["count"] for g in groups) == total
    return groups, notes
