"""Persona archetypes (research/EVAC_PSYCHOLOGY.md section 5, research/SCENARIOS_AND_MODELS.md).

All numbers here are our synthesis, not published values. OCEAN traits in [-1, 1].
`cred` is source credibility per persona: Lindell and Perry 2012 rate sources on
expertise, trustworthiness and protection responsibility and give no fixed ranking,
so each persona weights sources its own way (a warden in person can beat a PA).
`act_at` is the threat certainty above which the fallback policy acts.
"""

from __future__ import annotations

BASE_CRED = {"sight": 1.0, "smell": 0.9, "alarm": 0.5, "pa": 0.7, "warden": 0.8, "known": 0.6, "stranger": 0.35}

PERSONAS: dict[str, dict] = {
    "warden": {
        "text": "Floor fire warden on staff. Trained to treat every alarm as real, sweep your area, "
                "direct others to the nearest safe exit and leave your zone last. You act on the alarm alone.",
        "ocean": (0.2, 0.7, 0.5, 0.4, -0.4), "act_at": 0.25, "dependants": 0.6,
        "cred": {"alarm": 0.9, "pa": 0.9, "warden": 0.8, "known": 0.5, "stranger": 0.3},
        "say": "Everyone out, follow me to the exit",
    },
    "regular_employee": {
        "text": "You work here every day and know your usual way in plus one other exit. You tend to wait for "
                "confirmation before leaving and may finish what you are doing first. What makes you go: a warden "
                "telling you, seeing or smelling smoke, or most coworkers leaving.",
        "ocean": (0.0, 0.3, 0.1, 0.3, 0.0), "act_at": 0.5, "dependants": 0.0,
        "cred": {"warden": 0.85, "known": 0.65, "alarm": 0.5, "pa": 0.6},
    },
    "visitor": {
        "text": "First time in this building; you only know the way you came in. You look to staff and to what "
                "other people are doing. What makes you go: a staff instruction, or a crowd moving.",
        "ocean": (0.1, 0.0, 0.0, 0.3, 0.3), "act_at": 0.45, "dependants": 0.0,
        "cred": {"warden": 0.85, "stranger": 0.5, "pa": 0.7, "alarm": 0.5},
    },
    "committed_task": {
        "text": "You are in the middle of a task you care about (hosting a meeting, serving a queue). You keep at "
                "it until a person asks you to leave or you see the threat yourself.",
        "ocean": (0.0, 0.6, 0.2, 0.2, -0.1), "act_at": 0.65, "dependants": 0.2,
        "cred": {"warden": 0.9, "alarm": 0.35, "pa": 0.5},
    },
    "parent_with_child": {
        "text": "You are here with your young child. You move toward your child first and never leave without them.",
        "ocean": (0.0, 0.4, 0.0, 0.5, 0.4), "act_at": 0.35, "dependants": 1.0,
        "cred": {"known": 0.8, "warden": 0.7},
    },
    "older_adult": {
        "text": "Older adult, you walk slowly and stairs are hard. You rely on staff and announcements and stay "
                "with your companion.",
        "ocean": (-0.1, 0.4, -0.1, 0.4, 0.2), "act_at": 0.45, "dependants": 0.3,
        "cred": {"warden": 0.9, "pa": 0.85},
    },
    "mobility_impaired": {
        "text": "You use a mobility aid and cannot take stairs unaided. You wait for a helper or go to a refuge "
                "point, and you phone for help.",
        "ocean": (0.0, 0.4, 0.0, 0.3, 0.3), "act_at": 0.4, "dependants": 0.0,
        "cred": {"warden": 0.9, "pa": 0.8},
    },
    "skeptic": {
        "text": "You assume alarms are drills or false. You ignore the alarm until you see or smell smoke yourself, "
                "or two different people tell you to go.",
        "ocean": (-0.3, 0.2, 0.0, -0.3, -0.2), "act_at": 0.8, "dependants": 0.0,
        "cred": {"alarm": 0.15, "pa": 0.35, "warden": 0.5, "known": 0.3, "stranger": 0.15},
    },
}

ALIASES = {"regular": "regular_employee", "employee": "regular_employee", "staff": "warden",
           "first_time_visitor": "visitor", "parent": "parent_with_child", "older": "older_adult",
           "impaired": "mobility_impaired"}


def persona(name: str) -> tuple[str, dict]:
    """Canonical name and parameters; unknown names fall back to regular_employee."""
    key = ALIASES.get(name, name)
    return (key, PERSONAS[key]) if key in PERSONAS else ("regular_employee", PERSONAS["regular_employee"])


def credibility(persona_name: str, source_kind: str) -> float:
    _, p = persona(persona_name)
    return p["cred"].get(source_kind, BASE_CRED.get(source_kind, 0.35))


def _unit(x: float) -> float:
    return min(1.0, max(0.0, x))


def contagion_traits(persona_name: str) -> dict:
    """ASCRIBE openness (delta) and expressiveness (eps) from OCEAN, rescaled to [0, 1].

    Coefficients from van Haeringen and Gerritsen 2023 / Durupinar 2016 (correlations,
    not weights summing to 1), so the sum over traits in [-1, 1] is mapped by (x + 1) / 2.
    """
    o, c, e, a, n = persona(persona_name)[1]["ocean"]
    delta = 0.354 * o + 0.177 * c + 0.135 * e + 0.312 * a + 0.021 * n
    eps = 0.14 * o - 0.02 * c + 0.32 * e + 0.11 * a + 0.29 * n
    return {"delta": _unit((delta + 1) / 2), "eps": _unit((eps + 1) / 2)}


def baseline(persona_name: str) -> dict:
    """Resting emotion levels; higher neuroticism gives a higher anxiety floor and slower decay (tau)."""
    n = persona(persona_name)[1]["ocean"][4]
    return {"fear": 0.0, "anxiety": round(0.05 + 0.1 * max(0.0, n), 3), "frustration": 0.0, "hope": 0.2,
            "relief": 0.0, "concern": 0.0, "tau_s": 10 + 20 * (n + 1) / 2}
