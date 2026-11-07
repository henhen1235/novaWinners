"""Walking pace chosen by the decider: calm | brisk | hurry, as speed multipliers (m/s).

The multiplier applies to the agent's sampled speed, capped so nobody exceeds
what their persona allows (people who cannot use stairs do not speed up).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sim.agents import Agent

PACES = {"calm": 0.85, "brisk": 1.0, "hurry": 1.25}
DEFAULT_PACE = "brisk"
MAX_SPEED_MPS = 2.2
CARRY_LEVEL_MPS = 0.6
CARRY_STAIR_MPS = 0.3
MAX_STAIR_MPS = 1.0


def resolve_pace(res: dict) -> str:
    """Pace from a decide() result: top-level "pace" or record.decision.pace; brisk if absent or invalid."""
    rec = res.get("record") or {}
    dec = rec.get("decision") or {}
    for p in (res.get("pace"), dec.get("pace"), (dec.get("padm") or {}).get("pace") if isinstance(dec, dict) else None):
        if p in PACES:
            return p
    return DEFAULT_PACE


def pace_speed(a: "Agent", pace: str | None = None) -> float:
    """Level walking speed (m/s) for the agent at a pace."""
    base = a.traits["speed_mps"]
    mult = PACES.get(pace or a.pace, 1.0)
    if not a.traits["uses_stairs"]:
        mult = min(mult, 1.0)
    v = min(base * mult, MAX_SPEED_MPS)
    return min(v, CARRY_LEVEL_MPS) if a.carrying else v


def stair_pace_speed(a: "Agent", down: bool) -> float:
    """Speed along a stair (m/s): persona stair speed times pace, capped; carrying caps lower."""
    base = a.traits["stair_down_mps"] if down else a.traits["stair_up_mps"]
    v = min(base * PACES.get(a.pace, 1.0), MAX_STAIR_MPS)
    return min(v, CARRY_STAIR_MPS) if a.carrying else v
