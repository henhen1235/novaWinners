"""Persona traits from research/EVAC_PSYCHOLOGY.md section 5 (our synthesis, not a standard).

Speeds in m/s (normal, clipped at 0.3; stairs 0.6 down / 0.4 up, older adults
0.3 down), pre-movement and notice delays in s (lognormal: median, sigma). `knows_nearest_p` is the chance a persona also
knows the exit nearest its spawn room besides the one it entered by.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Persona:
    name: str
    speed_mean: float
    speed_sd: float
    premove_median: float
    premove_sigma: float
    notice_median: float
    knows_all: bool
    knows_nearest_p: float
    sign_follow_p: float
    social_threshold: int  # people seen moving that make this persona go early
    color: str
    stair_down: float = 0.6  # m/s along the stair
    stair_up: float = 0.4
    uses_stairs: bool = True  # False: waits at the stair landing (refuge) for a helper


PERSONAS: dict[str, Persona] = {p.name: p for p in [
    Persona("staff", 1.34, 0.26, 20, 0.5, 3, True, 1.0, 0.9, 1, "#2e7d32"),
    Persona("regular", 1.34, 0.26, 60, 0.7, 6, False, 1.0, 0.5, 3, "#1565c0"),
    Persona("visitor", 1.34, 0.26, 90, 0.8, 8, False, 0.3, 0.3, 3, "#f9a825"),
    Persona("committed", 1.20, 0.2, 120, 0.8, 10, False, 0.3, 0.3, 5, "#6a1b9a"),
    Persona("parent", 0.90, 0.2, 90, 0.8, 6, False, 1.0, 0.4, 4, "#ef6c00", 0.5, 0.35),
    Persona("older_adult", 0.80, 0.15, 120, 0.9, 10, False, 0.5, 0.3, 4, "#8d6e63", 0.3, 0.25),
    Persona("mobility_impaired", 0.60, 0.1, 120, 0.9, 10, False, 0.5, 0.3, 4, "#c62828", 0.3, 0.2, False),
    Persona("skeptic", 1.34, 0.26, 300, 1.0, 10, False, 1.0, 0.2, 99, "#455a64"),
    # A child stays with its parent; its own pre-movement only matters if it is alone.
    Persona("child", 1.0, 0.15, 90, 0.8, 8, False, 0.0, 0.0, 99, "#ec407a", 0.4, 0.3),
]}

ALIASES = {
    "warden": "staff", "fire_warden": "staff", "employee": "regular", "worker": "regular",
    "first_time_visitor": "visitor", "guest": "visitor", "customer": "visitor",
    "older": "older_adult", "elderly": "older_adult", "senior": "older_adult",
    "mobility": "mobility_impaired", "disabled": "mobility_impaired", "wheelchair": "mobility_impaired",
    "parent_with_child": "parent", "committed_task": "committed",
}


def resolve(name: str) -> Persona:
    """Persona for a scenario name or alias; unknown names fall back to 'regular'."""
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    return PERSONAS.get(ALIASES.get(key, key), PERSONAS["regular"])


def sample_traits(p: Persona, rng: np.random.Generator) -> dict:
    """Per-agent draws: speed_mps, premove_s, notice_s, sign_follow_p."""
    return {
        "speed_mps": round(float(max(0.3, rng.normal(p.speed_mean, p.speed_sd))), 3),
        "premove_s": round(float(p.premove_median * np.exp(rng.normal(0, p.premove_sigma))), 2),
        "notice_s": round(float(p.notice_median * np.exp(rng.normal(0, 0.5))), 2),
        "sign_follow_p": p.sign_follow_p,
        "social_threshold": p.social_threshold,
        "stair_down_mps": p.stair_down, "stair_up_mps": p.stair_up, "uses_stairs": p.uses_stairs,
    }

