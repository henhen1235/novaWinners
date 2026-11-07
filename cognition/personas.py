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
                "confirmation before leaving and may finish what you are doing first, but only until a second, different "
                "cue (coworkers leaving, someone telling you, smoke) adds to the alarm. What makes "
                "you go at once: a warden telling you, seeing or smelling smoke, or most coworkers leaving.",
        "ocean": (0.0, 0.3, 0.1, 0.3, 0.0), "act_at": 0.5, "dependants": 0.0,
        "cred": {"warden": 0.85, "known": 0.65, "alarm": 0.5, "pa": 0.6},
    },
    "visitor": {
        "text": "First time in this building; you only know the way you came in. You look to staff and to what "
                "other people are doing. What makes you go: a staff instruction, or people near you leaving.",
        "ocean": (0.1, 0.0, 0.0, 0.3, 0.3), "act_at": 0.45, "dependants": 0.0,
        "cred": {"warden": 0.85, "stranger": 0.5, "pa": 0.7, "alarm": 0.5},
    },
    "committed_task": {
        "text": "You are in the middle of a task you care about (hosting a meeting, serving a queue). You keep at "
                "it until a person asks you to leave, you see the threat yourself, or several cues pile up.",
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
                "or two different people tell you to go, or nearly everyone around you has left.",
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


OCEAN_KEYS = (("O", "openness"), ("C", "conscientiousness"), ("E", "extraversion"), ("A", "agreeableness"),
              ("N", "neuroticism"))


def ocean(persona_name: str, traits: dict | None = None) -> tuple[float, ...]:
    """Big Five in [-1, 1]: per-occupant values from the UI (traits.ocean as dict or list, or top-level
    O/C/E/A/N or full names) override the persona default trait by trait."""
    base = list(persona(persona_name)[1]["ocean"])
    t = traits or {}
    src = t.get("ocean")
    if isinstance(src, (list, tuple)) and len(src) == 5:
        src = dict(zip("OCEAN", src))
    src = {**{k: t[k] for pair in OCEAN_KEYS for k in pair if k in t}, **(src or {})}
    for i, (short, full) in enumerate(OCEAN_KEYS):
        v = src.get(short, src.get(full))
        if isinstance(v, (int, float)):
            base[i] = max(-1.0, min(1.0, float(v)))
    return tuple(base)


def contagion_traits(persona_name: str, traits: dict | None = None) -> dict:
    """ASCRIBE openness (delta) and expressiveness (eps) from OCEAN, rescaled to [0, 1].

    Coefficients from van Haeringen and Gerritsen 2023 / Durupinar 2016 (correlations,
    not weights summing to 1), so the sum over traits in [-1, 1] is mapped by (x + 1) / 2.
    """
    o, c, e, a, n = ocean(persona_name, traits)
    delta = 0.354 * o + 0.177 * c + 0.135 * e + 0.312 * a + 0.021 * n
    eps = 0.14 * o - 0.02 * c + 0.32 * e + 0.11 * a + 0.29 * n
    return {"delta": _unit((delta + 1) / 2), "eps": _unit((eps + 1) / 2)}


def baseline(persona_name: str, traits: dict | None = None) -> dict:
    """Resting emotion levels; higher neuroticism gives a higher anxiety floor and slower decay (tau)."""
    n = ocean(persona_name, traits)[4]
    return {"fear": 0.0, "anxiety": round(0.05 + 0.1 * max(0.0, n), 3), "frustration": 0.0, "hope": 0.2,
            "relief": 0.0, "concern": 0.0, "tau_s": 10 + 20 * (n + 1) / 2}


def _level(v: float, words: tuple[str, str, str]) -> str:
    return words[0] if v <= -0.35 else words[2] if v >= 0.35 else words[1]


def trait_text(persona_name: str, traits: dict | None) -> str:
    """One plain sentence describing this occupant's sliders, for the prompt (persona > model)."""
    t = traits or {}
    o, c, e, a, n = ocean(persona_name, t)
    bits = [f"{_level(o, ('conventional', 'fairly open', 'curious and open'))}",
            f"{_level(c, ('easygoing about rules', 'moderately careful', 'careful and rule-following'))}",
            f"{_level(e, ('quiet and reserved', 'moderately social', 'outgoing and talkative'))}",
            f"{_level(a, ('independent-minded', 'cooperative', 'very cooperative and helpful'))}",
            f"{_level(n, ('calm under stress', 'somewhat anxious under stress', 'easily alarmed'))}"]
    out = [f"Personality: {', '.join(bits)} (Big Five O {o:+.1f} C {c:+.1f} E {e:+.1f} A {a:+.1f} N {n:+.1f})."]
    if t.get("speed_mps") is not None:
        sp = float(t["speed_mps"])
        out.append(f"You walk {'slowly' if sp < 1.0 else 'briskly' if sp > 1.5 else 'at a normal pace'} "
                   f"({sp:.1f} m/s).")
    if t.get("exit_familiarity") is not None:
        f = float(t["exit_familiarity"])
        out.append("You know this building's exits " + ("well." if f >= 0.66 else "a little." if f >= 0.33
                                                         else "hardly at all."))
    if t.get("sign_follow_p") is not None:
        out.append(f"You {'usually' if float(t['sign_follow_p']) >= 0.6 else 'rarely'} follow exit signs.")
    if t.get("social_threshold") is not None:
        out.append(f"You start to go along once about {int(t['social_threshold'])} people near you are leaving.")
    if t.get("risk_tolerance") is not None:
        r = float(t["risk_tolerance"])
        out.append("You " + ("take risks easily." if r >= 0.66 else "avoid risks." if r <= 0.33 else
                             "take ordinary care.") )
    return " ".join(out)
