"""Deterministic beliefs, appraisal, emotion and contagion (research/COGNITION_SOURCES.md).

Every function here is pure: the same inputs give the same outputs, which is what
lets interviews re-run a recorded decision with evidence removed.
"""

from __future__ import annotations

import math
import re

from .personas import baseline, contagion_traits, credibility, persona

HAZARD_KINDS = {"fire": 1.0, "smoke": 0.9, "intruder": 0.95, "gunshot": 0.85}
CLAIM_WEIGHT = {"hazard": 0.7, "told_to_leave": 0.6, "exit_blocked": 0.3}
ALARM_WEIGHT, PA_WEIGHT, SOCIAL_WEIGHT = 0.6, 0.8, 0.6
ALARM_REPEAT_WEIGHT, ALARM_REPEAT_S = 0.5, 120.0  # ours, calibration knobs
COPING_T_S = 90.0  # time scale for "an exit this far away still works"; ours, calibrate
CONTAGION_WINDOW_S = 2.0  # ponytail: neighbours sampled only at decision time; integrate over 2 s, not the gap
EVAC_ACTIONS = {"EVACUATE", "FOLLOW", "TURN_BACK", "TAKE_STAIRS", "USE_ELEVATOR"}
PROTECTIVE = EVAC_ACTIONS | {"HIDE", "SHELTER", "WAIT_REFUGE"}
STAIR_KINDS = {"stair", "stairwell", "stairs"}
CANNOT_STAIRS = {"mobility_impaired"}
OBSERVED = {"sight", "smell", "observation"}  # "observation" is the sim's own-eyes source kind


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(hi, max(lo, x))


def noisy_or(ws: list[float]) -> float:
    p = 1.0
    for w in ws:
        p *= 1.0 - clip(w)
    return 1.0 - p


# ---------- beliefs ----------

_EXIT_RE = re.compile(r"\b(?:e_?0*(\d+)|exit\s*(\d+))\b", re.I)


def _mentioned_exit(t: str, exits: dict[str, str]) -> str | None:
    for eid, label in exits.items():
        if eid.lower() in t or (label and label.lower() in t):
            return eid
    m = _EXIT_RE.search(t)
    if m:
        n = int(m.group(1) or m.group(2))
        hit = [e for e in exits if re.search(r"(\d+)$", e) and int(re.search(r"(\d+)$", e).group(1)) == n]
        return hit[0] if hit else f"e_{n:03d}"
    return None


def parse_claims(text: str, exits: dict[str, str] | None = None) -> dict:
    """Crude claims from free speech (exit id, label or "exit 2"), so a `say` can spread a rumor.
    ponytail: keywords only; ask the LLM for structured claims if rumors need nuance."""
    t = (text or "").lower()
    out: dict = {}
    for i, w in enumerate(("one", "two", "three", "four", "five", "six", "seven", "eight", "nine"), 1):
        t = re.sub(rf"\bexit {w}\b", f"exit {i}", t)
    eid = _mentioned_exit(t, exits or {})
    if eid and re.search(r"block|lock|closed|shut|can't get|cannot get|jammed", t):
        out["exit_blocked"] = eid
    if re.search(r"\bfire\b|smoke|burning|gun|shoot", t):
        out["hazard"] = "reported"
    if re.search(r"\b(get out|leave|evacuate|go now|move|run|out now|this way|follow me)\b", t):
        out["told_to_leave"] = True
    return out


def _evidence(p: dict, persona_name: str) -> list[tuple[str, bool, float, dict]]:
    """(belief key, value, weight, source) items a percept supports."""
    kind, ref, ch = p.get("kind"), p.get("ref"), p.get("channel", "sight")
    direct = {"kind": "smell" if ch == "smell" else "sight", "ref": p["id"]}
    if kind in HAZARD_KINDS:
        return [(f"hazard:{p.get('where') or ref or 'here'}", True, 1.0, direct)]
    if kind in ("exit_blocked", "blocked") and ref:
        return [(f"exit_open:{ref}", False, 1.0, direct)]
    if kind == "exit" and ref and ch == "sight" and "blocked" in p:
        return [(f"exit_open:{ref}", not p["blocked"], 1.0, direct)]
    if kind in STAIR_KINDS and ref:
        bad = bool(p.get("smoke") or p.get("blocked"))
        out = [(f"stair_ok:{ref}", not bad, 1.0, direct)]
        if p.get("smoke"):
            out.append((f"hazard:{p.get('where') or ref}", True, 1.0, direct))
        return out
    if kind == "alarm":
        return [("alarm", True, credibility(persona_name, "alarm"), {"kind": "alarm", "ref": p["id"]})]
    if kind in ("pa", "utterance", "said", "shout", "message"):
        sk = "pa" if kind == "pa" else p.get("source_kind", "stranger")
        w = credibility(persona_name, sk)
        src = {"kind": sk, "ref": p["id"], "from": p.get("from")}
        claims = p.get("claim") or parse_claims(p.get("text", ""))
        out = []
        blocked = claims.get("exit_blocked")
        for eid in [blocked] if isinstance(blocked, str) else (blocked or []):
            out.append((f"exit_open:{eid}", False, w, src))
        if claims.get("exit_open"):
            out.append((f"exit_open:{claims['exit_open']}", True, w, src))
        if claims.get("hazard"):
            out.append((f"hazard:{claims['hazard']}", True, w, src))
        if claims.get("told_to_leave") or kind == "pa" or p.get("urgent"):
            out.append(("told_to_leave", True, w, src))
        return out
    return []


def _next_id(beliefs: list[dict]) -> str:
    nums = [int(b["id"][1:]) for b in beliefs if re.fullmatch(r"b\d+", b["id"])]
    return f"b{max(nums, default=0) + 1}"


def update_beliefs(before: list[dict], percepts: list[dict], persona_name: str, tick: int) -> list[dict]:
    """New belief list. Direct observation overrides hearsay; hearsay never overrides observation.

    Agreeing hearsay: conf = noisy-or(conf, w). Conflicting hearsay flips the value only if w > conf.
    """
    out = [dict(b) for b in before]
    for p in percepts:
        for key, value, w, src in _evidence(p, persona_name):
            cur = next((b for b in out if b["key"] == key), None)
            observed = src["kind"] in OBSERVED
            new = {"key": key, "value": value, "source": src, "tick": tick}
            if cur is None:
                out.append({"id": _next_id(out), **new, "conf": round(w, 3)})
                continue
            cur_observed = cur["source"]["kind"] in OBSERVED
            if observed:
                cur.update(new, conf=1.0)
            elif cur_observed and cur["value"] != value:
                continue
            elif cur["value"] == value and (cur["source"]["kind"], cur["source"].get("from")) == (src["kind"], src.get("from")):
                cur["conf"] = max(cur["conf"], round(w, 3))  # hearing the same source again is not new evidence
            elif cur["value"] == value:
                cur["conf"] = round(noisy_or([cur["conf"], w]), 3)
            elif w > cur["conf"]:
                cur.update(new, conf=round(w, 3))
            else:
                cur["conf"] = round(cur["conf"] * (1 - w), 3)
    return out


# ---------- appraisal ----------

def social_proof(percepts: list[dict], threshold: float = 4.0) -> float:
    """Share of visible people leaving; the aggregate people_moving cue saturates at the occupant's
    social threshold (default 4 movers)."""
    agg = [p for p in percepts if p.get("kind") == "people_moving"]
    if agg:
        return clip(max(float(p.get("count") or 0) for p in agg) / max(1.0, threshold))
    people = [p for p in percepts if p.get("kind") == "person"]
    if not people:
        return 0.0
    moving = sum(1 for p in people if p.get("doing") in ("evacuating", "moving_to_exit", "leaving", "exited"))
    return moving / len(people)


def exit_open_prob(beliefs: list[dict], exit_id: str | None) -> float:
    b = next((b for b in beliefs if b["key"] == f"exit_open:{exit_id}"), None)
    if b is None:
        return 0.9
    return 1.0 - b["conf"] if b["value"] is False else 1.0


def stair_ok_prob(beliefs: list[dict], o: dict) -> float:
    """Chance a stair option is usable: 0 if the sim marks it blocked, discounted by a belief it is smoky/blocked."""
    if o.get("blocked") or o.get("smoke"):
        return 0.0
    b = next((b for b in beliefs if b["key"] == f"stair_ok:{o.get('stair')}"), None)
    return 1.0 - b["conf"] if b is not None and b["value"] is False else 1.0


def stair_crowd_s(o: dict) -> float:
    c = o.get("crowding")
    return {"low": 0.0, "medium": 15.0, "high": 40.0}.get(c, float(c) * 40 if isinstance(c, (int, float)) else 0.0)


def option_success(o: dict, beliefs: list[dict], persona_name: str = "", hazard: bool = False,
                   traits: dict | None = None) -> float:
    """Estimated success of an option. Walking speed sets travel time; exit familiarity raises
    confidence that a known route works (0.8 at none, 1.0 at full)."""
    t = traits or {}
    speed = float(t.get("speed_mps") or 1.3)
    known = 0.8 + 0.2 * float(t["exit_familiarity"]) if t.get("exit_familiarity") is not None else 1.0
    return known * _success(o, beliefs, persona_name, hazard, speed)


def _success(o: dict, beliefs: list[dict], persona_name: str, hazard: bool, speed: float) -> float:
    a = o.get("action")
    if a == "TAKE_STAIRS":
        if persona(persona_name)[0] in CANNOT_STAIRS:
            return 0.05
        t = float(o.get("route_m") or 20.0) / max(0.3, speed * 0.75) + float(o.get("queue_s") or 0.0) + stair_crowd_s(o)
        return math.exp(-t / COPING_T_S) * stair_ok_prob(beliefs, o) * (1.0 if o.get("direction", "down") == "down"
                                                                       else 0.6)
    if a == "USE_ELEVATOR":
        return 0.5 if (o.get("usable_in_fire") or not hazard) and not o.get("blocked") else 0.05
    if a == "WAIT_REFUGE":
        return 0.45
    if a == "EVACUATE":
        t = float(o.get("route_m") or 20.0) / max(0.3, speed) + float(o.get("queue_s") or 0.0)
        return math.exp(-t / COPING_T_S) * exit_open_prob(beliefs, o.get("exit"))
    return {"FOLLOW": 0.6, "TURN_BACK": 0.5, "HIDE": 0.4, "SHELTER": 0.5}.get(a, 0.0)


def appraise(percepts: list[dict], beliefs: list[dict], options: list[dict], persona_name: str,
             trigger: dict, traits: dict | None = None) -> dict:
    """R, S, U, C, B plus social proof and protection responsibility; H = R*S*(1-C)."""
    traits = traits or {}
    sp = social_proof(percepts, float(traits.get("social_threshold") or 4.0))
    cues = []
    for b in beliefs:
        if not b["value"]:
            continue
        if b["key"].startswith("hazard:"):
            cues.append(b["conf"] * (0.95 if b["source"]["kind"] in OBSERVED else CLAIM_WEIGHT["hazard"]))
        elif b["key"] == "alarm":
            cues.append(ALARM_WEIGHT * b["conf"])
        elif b["key"] == "told_to_leave":
            cues.append((PA_WEIGHT if b["source"]["kind"] == "pa" else CLAIM_WEIGHT["told_to_leave"]) * b["conf"])
    # people leaving: a PADM social cue; skeptics discount it (their persona needs two people or the threat)
    cues.append(SOCIAL_WEIGHT * sp * (0.4 if persona(persona_name)[0] == "skeptic" else 1.0))
    # an alarm that keeps ringing is repeated warning, which raises belief (Mileti and Peek: repetition)
    ring = max((float(p.get("t_since_s") or 0) for p in percepts if p.get("kind") == "alarm"), default=0.0)
    cues.append(ALARM_REPEAT_WEIGHT * min(1.0, ring / ALARM_REPEAT_S) * min(1.0, credibility(persona_name, "alarm") / 0.5))
    s = noisy_or(cues)

    hazards = [p for p in percepts if p.get("kind") in HAZARD_KINDS]
    near = min((float(p.get("dist_m") or 30.0) for p in hazards), default=None)
    resp = clip(max(persona(persona_name)[1]["dependants"], float(traits.get("dependants", 0.0))))
    if near is not None:
        r, u = (1.0 if near < 15 else 0.7), clip(1 - near / 25, 0.2, 1.0)
    else:
        r, u = (0.5 if s > 0 else 0.2), (0.3 if s > 0 else 0.1)
    r = clip(r + 0.3 * resp)
    if traits.get("risk_tolerance") is not None:  # ours: risk-tolerant people rate the threat as less relevant
        r = clip(r * (1.2 - 0.4 * float(traits["risk_tolerance"])))

    hazard = any(b["value"] and b["key"].startswith("hazard:") for b in beliefs)
    c = max((option_success(o, beliefs, persona_name, hazard, traits) for o in options if o.get("action") in PROTECTIVE),
            default=0.1)
    blocked = 1.0 if trigger.get("kind") in ("path_blocked", "blocked") else 0.0
    stairs = [o for o in options if o.get("action") == "TAKE_STAIRS" and o.get("direction", "down") == "down"]
    if stairs:  # a smoke-filled or blocked stairwell obstructs the way out (Scherer goal obstruction)
        bad = [1.0 - stair_ok_prob(beliefs, o) for o in stairs]
        blocked = max(blocked, min(bad), 0.5 * max(bad))
    target = traits.get("target_exit")
    if target:
        blocked = max(blocked, 1.0 - exit_open_prob(beliefs, target))
    if blocked:
        u = clip(u + 0.2)
    h = r * s * (1 - c)
    return {k: round(v, 4) for k, v in {"relevance": r, "certainty": s, "urgency": u, "coping": c,
            "goal_blocked": blocked, "social_proof": sp, "protection_responsibility": resp, "harm": h}.items()}


# ---------- emotion ----------

def appraisal_emotions(a: dict, h_prev: float) -> dict:
    r, s, u, c, b, h = (a[k] for k in ("relevance", "certainty", "urgency", "coping", "goal_blocked", "harm"))
    return {"fear": h * u, "anxiety": r * 4 * s * (1 - s) * (1 - c), "hope": r * s * c,
            "relief": max(0.0, h_prev - h), "frustration": r * b, "concern": a["protection_responsibility"] * h}


def decay(e: float, b: float, dt: float, tau: float) -> float:
    return b + (e - b) * math.exp(-max(0.0, dt) / tau)


def contagion(q: float, delta_r: float, neighbours: list[dict], key: str) -> tuple[float, list[dict]]:
    """ASCRIBE absorption only (eta = 0): q += gamma_R * dt * (q* - q), gamma_R * dt clamped to 1."""
    gammas = []
    for nb in neighbours:
        d = float(nb.get("dist_m") or 0.0)
        tau_d = 10.0 if nb.get("channel") == "hearing" else 4.0
        alpha = 1 - 1 / (1 + math.exp(-1.5 * (d - tau_d)))
        gammas.append((nb, nb.get("eps", 0.5) * alpha * delta_r))
    g = sum(x for _, x in gammas)
    if g <= 1e-9:
        return q, []
    q_star = sum(x * nb.get(key, 0.0) for nb, x in gammas) / g
    step = min(1.0, g * CONTAGION_WINDOW_S)
    new = q + step * (q_star - q)
    src = [{"emotion": key, "from": "contagion", "agent": nb.get("agent"),
            "delta": round(step * x / g * (nb.get(key, 0.0) - q), 4)} for nb, x in gammas
           if abs(step * x / g * (nb.get(key, 0.0) - q)) >= 0.005]
    return new, src


def emotions(appraisal: dict, mind: dict | None, t_s: float, persona_name: str,
             neighbours: list[dict], traits: dict | None = None) -> tuple[dict, list[dict]]:
    """Decay prior emotion toward the persona baseline, raise it to the appraisal value, then contagion.

    Appraisal can only push fear, anxiety, frustration and concern up; they fall by decay
    (emotions outlast their cause). Hope tracks the appraisal; relief is a decaying pulse.
    """
    base = baseline(persona_name, traits)
    mind = mind or {}
    prev = mind.get("emotion") or {k: base[k] for k in ("fear", "anxiety", "frustration", "hope", "relief", "concern")}
    dt = t_s - mind.get("t_s", t_s)
    app = appraisal_emotions(appraisal, mind.get("harm", 0.0))
    out, sources = {}, []
    for k, v in app.items():
        d = decay(prev.get(k, base[k]), base[k], dt, base["tau_s"])
        out[k] = v if k == "hope" else max(d, v)
        if out[k] - d > 1e-4:
            sources.append({"emotion": k, "from": "appraisal", "delta": round(out[k] - d, 4)})
    delta_r = contagion_traits(persona_name, traits)["delta"]
    for k in ("fear", "anxiety"):
        out[k], src = contagion(out[k], delta_r, neighbours, k)
        sources += src
    out = {k: round(clip(v), 4) for k, v in out.items()}
    neg = max(out["fear"], out["anxiety"], out["frustration"])
    out["valence"] = round(clip(out["hope"] + out["relief"] - neg - 0.5 * out["concern"], -1.0, 1.0), 4)
    out["arousal"] = round(clip(max(neg, out["concern"], 0.5 * out["relief"])), 4)
    return out, sources


def dominant(e: dict) -> str:
    keys = ("fear", "anxiety", "frustration", "hope", "relief", "concern")
    k = max(keys, key=lambda x: e[x])
    return k if e[k] >= 0.15 else "calm"
