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
COPING_T_S = 90.0  # time scale for "an exit this far away still works"; ours, calibrate
CONTAGION_WINDOW_S = 2.0  # ponytail: neighbours sampled only at decision time; integrate over 2 s, not the gap
EVAC_ACTIONS = {"EVACUATE", "FOLLOW", "TURN_BACK"}
PROTECTIVE = EVAC_ACTIONS | {"HIDE", "SHELTER"}


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(hi, max(lo, x))


def noisy_or(ws: list[float]) -> float:
    p = 1.0
    for w in ws:
        p *= 1.0 - clip(w)
    return 1.0 - p


# ---------- beliefs ----------

_EXIT_RE = re.compile(r"\b(?:e_?0*(\d+)|exit\s*(\d+))\b", re.I)


def parse_claims(text: str) -> dict:
    """Crude claims from free speech, so an agent's `say` can spread a rumor. ponytail: keywords only."""
    t = (text or "").lower()
    out: dict = {}
    m = _EXIT_RE.search(t)
    if m and re.search(r"block|lock|closed|shut|can't get|cannot get|jammed", t):
        out["exit_blocked"] = f"e_{int(m.group(1) or m.group(2)):03d}"
    if re.search(r"\bfire\b|smoke|burning|gun|shoot", t):
        out["hazard"] = "reported"
    if re.search(r"\b(get out|leave|evacuate|go now|move|run|out now|this way|follow me)\b", t):
        out["told_to_leave"] = True
    return out


def _evidence(p: dict, persona_name: str) -> list[tuple[str, bool, float, dict]]:
    """(belief key, value, weight, source) items a percept supports."""
    kind, ref, ch = p.get("kind"), p.get("ref"), p.get("channel", "sight")
    direct = {"kind": ch if ch in ("sight", "smell") else "sight", "ref": p["id"]}
    if kind in HAZARD_KINDS:
        return [(f"hazard:{p.get('where') or ref or 'here'}", True, 1.0, direct)]
    if kind in ("exit_blocked", "blocked") and ref:
        return [(f"exit_open:{ref}", False, 1.0, direct)]
    if kind == "exit" and ref and ch == "sight" and "blocked" in p:
        return [(f"exit_open:{ref}", not p["blocked"], 1.0, direct)]
    if kind == "alarm":
        return [("alarm", True, credibility(persona_name, "alarm"), {"kind": "alarm", "ref": p["id"]})]
    if kind in ("pa", "utterance", "said", "shout"):
        sk = "pa" if kind == "pa" else p.get("source_kind", "stranger")
        w = credibility(persona_name, sk)
        src = {"kind": sk, "ref": p["id"], "from": p.get("from")}
        claims = p.get("claim") or parse_claims(p.get("text", ""))
        out = []
        if claims.get("exit_blocked"):
            out.append((f"exit_open:{claims['exit_blocked']}", False, w, src))
        if claims.get("exit_open"):
            out.append((f"exit_open:{claims['exit_open']}", True, w, src))
        if claims.get("hazard"):
            out.append((f"hazard:{claims['hazard']}", True, w, src))
        if claims.get("told_to_leave") or kind == "pa":
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
            observed = src["kind"] in ("sight", "smell")
            new = {"key": key, "value": value, "source": src, "tick": tick}
            if cur is None:
                out.append({"id": _next_id(out), **new, "conf": round(w, 3)})
                continue
            cur_observed = cur["source"]["kind"] in ("sight", "smell")
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

def social_proof(percepts: list[dict]) -> float:
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


def option_success(o: dict, beliefs: list[dict]) -> float:
    a = o.get("action")
    if a == "EVACUATE":
        t = float(o.get("route_m") or 20.0) / 1.3 + float(o.get("queue_s") or 0.0)
        return math.exp(-t / COPING_T_S) * exit_open_prob(beliefs, o.get("exit"))
    return {"FOLLOW": 0.6, "TURN_BACK": 0.5, "HIDE": 0.4, "SHELTER": 0.5}.get(a, 0.0)


def appraise(percepts: list[dict], beliefs: list[dict], options: list[dict], persona_name: str,
             trigger: dict, traits: dict | None = None) -> dict:
    """R, S, U, C, B plus social proof and protection responsibility; H = R*S*(1-C)."""
    traits = traits or {}
    sp = social_proof(percepts)
    cues = []
    for b in beliefs:
        if not b["value"]:
            continue
        if b["key"].startswith("hazard:"):
            cues.append(b["conf"] * (0.95 if b["source"]["kind"] in ("sight", "smell") else CLAIM_WEIGHT["hazard"]))
        elif b["key"] == "alarm":
            cues.append(ALARM_WEIGHT * b["conf"])
        elif b["key"] == "told_to_leave":
            cues.append((PA_WEIGHT if b["source"]["kind"] == "pa" else CLAIM_WEIGHT["told_to_leave"]) * b["conf"])
    cues.append(SOCIAL_WEIGHT * sp * credibility(persona_name, "stranger"))
    s = noisy_or(cues)

    hazards = [p for p in percepts if p.get("kind") in HAZARD_KINDS]
    near = min((float(p.get("dist_m") or 30.0) for p in hazards), default=None)
    resp = clip(max(persona(persona_name)[1]["dependants"], float(traits.get("dependants", 0.0))))
    if near is not None:
        r, u = (1.0 if near < 15 else 0.7), clip(1 - near / 25, 0.2, 1.0)
    else:
        r, u = (0.5 if s > 0 else 0.2), (0.3 if s > 0 else 0.1)
    r = clip(r + 0.3 * resp)

    c = max((option_success(o, beliefs) for o in options if o.get("action") in PROTECTIVE), default=0.1)
    blocked = 1.0 if trigger.get("kind") in ("path_blocked", "blocked") else 0.0
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
            "delta": round(step * x / g * (nb.get(key, 0.0) - q), 4)} for nb, x in gammas if x > 1e-6]
    return new, src


def emotions(appraisal: dict, mind: dict | None, t_s: float, persona_name: str,
             neighbours: list[dict]) -> tuple[dict, list[dict]]:
    """Decay prior emotion toward the persona baseline, raise it to the appraisal value, then contagion.

    Appraisal can only push fear, anxiety, frustration and concern up; they fall by decay
    (emotions outlast their cause). Hope tracks the appraisal; relief is a decaying pulse.
    """
    base = baseline(persona_name)
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
    delta_r = contagion_traits(persona_name)["delta"]
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
