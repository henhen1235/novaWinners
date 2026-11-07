"""Occupancy seeding: an LLM proposes who is in each room, code bounds it.

The model reads each room's type, name, size, doors and furniture
(ingest_lidar.semantics._describe) and proposes a count and persona mix; code
clamps the count to the room's IBC occupant load (analysis/occupancy.py) and
drops unknown personas. "The model proposes; code checks." Without an LLM
(or on any failure) every room gets half its load with the default mix.
"""

from __future__ import annotations

import json
import math

from analysis.occupancy import analyze as occupancy
from ingest_lidar.semantics import _describe, complete

from .schema import DEFAULT_MODEL, DEFAULT_PERSONAS, PERSONAS

SEED_MODEL = DEFAULT_MODEL
ARCHETYPES = PERSONAS[:8]
FALLBACK_FILL = 0.5  # share of the code load seeded without an LLM [H]


def _mix(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_PERSONAS)
    kept = {k: float(v) for k, v in raw.items() if k in ARCHETYPES and isinstance(v, (int, float)) and v > 0}
    total = sum(kept.values())
    if not total:
        return dict(DEFAULT_PERSONAS)
    mix = {k: round(v / total, 3) for k, v in kept.items()}
    top = max(mix, key=mix.get)
    mix[top] = round(mix[top] + 1 - sum(mix.values()), 3)  # rounding drift goes to the largest share
    return mix


def _prompt(scene: dict, loads: dict[str, int]) -> str:
    names = {r["id"]: (r["name"], r["type"]) for r in scene["rooms"]}
    rooms = [{**d, "name": names[d["id"]][0], "type": names[d["id"]][1], "max_occupants": loads[d["id"]]}
             for d in _describe(scene) if loads.get(d["id"], 0) > 0]
    return ("You place people in a building for an evacuation drill during a normal weekday afternoon. "
            "For each room propose a realistic count (0..max_occupants; max is the fire-code design load, "
            "real rooms are usually well below it) and a persona mix from "
            f"{list(ARCHETYPES)} (weights sum to 1), based on the room type, size and furniture. "
            'Return JSON {"rooms": [{"id": "...", "count": 0, "personas": {"regular_employee": 1.0}, '
            '"why": "max 12 words"}]}.\nRooms: ' + json.dumps(rooms))


def seed(scene: dict, use_llm: bool = True, model: str = SEED_MODEL) -> tuple[list[dict], list[str]]:
    """Return (occupants block, assumptions)."""
    loads = {k: v["load"] for k, v in occupancy(scene)["rooms"].items()}
    notes = ["counts bounded by IBC occupant loads (analysis/occupancy.py, factors unverified)"]
    got: dict[str, dict] = {}
    if use_llm:
        try:
            got = {x["id"]: x for x in json.loads(complete(_prompt(scene, loads), model=model, max_tokens=3000))["rooms"]}
            notes.append(f"occupants proposed by {model}")
        except Exception as e:  # degrade to the code default, but say so
            notes.append(f"LLM seeding failed ({type(e).__name__}: {str(e)[:120]}); used {FALLBACK_FILL:.0%} of load")
    else:
        notes.append(f"no LLM: each room seeded at {FALLBACK_FILL:.0%} of its load, default persona mix")
    out = []
    for rid, cap in loads.items():
        if cap <= 0:
            continue
        g = got.get(rid)
        if g is not None:
            try:
                count = max(0, min(cap, int(g.get("count", 0))))
            except (TypeError, ValueError):
                count = math.ceil(cap * FALLBACK_FILL)
            mix, why = _mix(g.get("personas")), str(g.get("why", ""))[:80]
        else:
            count, mix, why = math.ceil(cap * FALLBACK_FILL), dict(DEFAULT_PERSONAS), "default"
        if count:
            out.append({"where": {"room": rid}, "count": count, "personas": mix, "models": {DEFAULT_MODEL: 1.0}})
            notes.append(f"{rid}: {count} of max {cap} ({why})")
    return out, notes
