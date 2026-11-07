"""How long an occupant may keep waiting (calibration to research/EVAC_PSYCHOLOGY.md).

The LLM chooses HOW to respond; code bounds HOW LONG waiting can last:
- a per-agent deadline from the persona's lognormal pre-movement delay (sampled by the sim as
  traits.premove_s, else here), stretched by DEADLINE_FACTOR so cues can still make people leave
  earlier and nobody stalls forever;
- SEEK_INFO at most MAX_SEEK times, CONTINUE_TASK only while the persona's own override rule allows.
Waiting options that are no longer allowed are not offered (a subset of the sim's options).
"""

from __future__ import annotations

import hashlib
import math

from .personas import persona

DEADLINE_FACTOR = 1.5  # calibration knob: deadline = factor x sampled pre-movement delay
MAX_SEEK = 2
WAIT_ACTIONS = {"SEEK_INFO", "CONTINUE_TASK"}
# (median s, sigma) of pre-movement, EVAC_PSYCHOLOGY.md section 5 (office drill values)
PREMOVE = {"warden": (20, 0.5), "regular_employee": (60, 0.7), "visitor": (90, 0.8), "committed_task": (120, 0.8),
           "parent_with_child": (90, 0.8), "older_adult": (120, 0.9), "mobility_impaired": (120, 0.9),
           "skeptic": (300, 1.0)}
# CONTINUE_TASK ends at the first of: this many distinct cue kinds, or this many seconds since the first cue
TASK_RULE = {"warden": (1, 0), "regular_employee": (2, 120), "visitor": (2, 90), "committed_task": (3, 180),
             "parent_with_child": (1, 30), "older_adult": (2, 120), "mobility_impaired": (2, 120),
             "skeptic": (3, 300)}


def _normal(seed: str) -> float:
    h = hashlib.sha256(seed.encode()).digest()
    u1, u2 = (int.from_bytes(h[:4], "big") + 1) / 2**32, int.from_bytes(h[4:8], "big") / 2**32
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


def premove_s(agent: dict) -> float:
    traits = agent.get("traits") or {}
    if traits.get("premove_s") is not None:
        return float(traits["premove_s"])
    med, sig = PREMOVE[persona(agent.get("persona", ""))[0]]
    return med * math.exp(sig * _normal(f"premove|{agent['id']}"))


def cue_summary(percepts: list[dict], beliefs: list[dict]) -> dict:
    """Cues accumulated so far: what the occupant has to go on, by kind."""
    alarm = [p for p in percepts if p.get("kind") == "alarm"]
    movers = max((int(p.get("count") or 0) for p in percepts if p.get("kind") == "people_moving"), default=0)
    movers += sum(1 for p in percepts if p.get("kind") == "person" and p.get("doing") in ("evacuating", "leaving"))
    told = [b for b in beliefs if b["key"] == "told_to_leave" and b["value"]]
    staff = any(b["source"].get("kind") in ("warden", "pa") for b in told)
    hazard = any(b["key"].startswith("hazard:") and b["value"] for b in beliefs)
    kinds = {k for k, on in (("alarm", alarm), ("people_leaving", movers), ("told", told), ("hazard", hazard)) if on}
    return {"alarm_s": max((float(p.get("t_since_s") or 0) for p in alarm), default=None), "people_leaving": movers,
            "told_to_leave": len(told), "staff_or_pa": staff, "hazard": hazard, "distinct": len(kinds)}


def limits(agent: dict, mind: dict, cues: dict, options: list[dict]) -> dict:
    name = persona(agent.get("persona", ""))[0]
    traits = agent.get("traits") or {}
    since = float(traits.get("t_since_cue_s") or cues.get("alarm_s") or 0.0)
    deadline = DEADLINE_FACTOR * premove_s(agent)
    waits = mind.get("waits") or {}
    n_cues, max_s = TASK_RULE[name]
    allowed = set()
    if since < deadline:
        if waits.get("SEEK_INFO", 0) < MAX_SEEK:
            allowed.add("SEEK_INFO")
        if cues["distinct"] < n_cues and since < max_s and not (cues["staff_or_pa"] and name != "skeptic"):
            allowed.add("CONTINUE_TASK")
    acting = [o for o in options if o.get("action") not in WAIT_ACTIONS]
    offered = [o for o in options if o.get("action") not in WAIT_ACTIONS or o.get("action") in allowed or not acting]
    return {"since_cue_s": round(since, 1), "deadline_s": round(deadline, 1), "overdue": since >= deadline,
            "waits": dict(waits), "allowed_waits": sorted(allowed), "offered": [o["id"] for o in offered]}
