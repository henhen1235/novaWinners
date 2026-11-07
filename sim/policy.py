"""Default rule-based decide() (no LLM) and the shortest-path baseline.

decide(view) -> {"choice": option_id, "say": str | None, "record": {...}}, with
`view` as in docs/SIM_INTERFACES.md. Times in s, distances in m.

Rules (research/EVAC_PSYCHOLOGY.md): people delay until their sampled
pre-movement time ends unless a strong cue cuts it short (fire or smoke seen,
an urgent message, enough people seen moving); skeptics ignore the alarm until
they see smoke or two people urge them; everyone avoids exits they believe are
blocked and prefers the exit they came in by.
"""

from __future__ import annotations

FAMILIAR_BONUS_S = 15.0  # how much longer a route to the entry exit may be and still win


def _blocked_beliefs(view: dict) -> set[str]:
    out = set()
    for b in view.get("beliefs", []):
        if b["key"].startswith("exit_open:") and b["value"] is False and b.get("conf", 0) >= 0.5:
            out.add(b["key"].split(":", 1)[1])
    return out


def _go_reason(view: dict) -> tuple[str | None, list[str]]:
    """Why this agent moves now (or None), plus the percept ids that justify it."""
    a, per = view["agent"], view.get("percepts", [])
    tr = a["traits"]
    hazard = [p["id"] for p in per if p["kind"] in ("fire", "smoke")]
    urged = [p["id"] for p in per if p["kind"] == "message" and p.get("urgent")]
    moving = [p for p in per if p["kind"] == "people_moving"]
    n_moving = max((p["count"] for p in moving), default=0)
    alarm = [p["id"] for p in per if p["kind"] == "alarm"]
    if hazard:
        return "hazard", hazard
    if a["persona"] == "skeptic":
        if len(urged) >= 2:
            return "urged", urged
        return ("delay_over", alarm) if tr["t_since_cue_s"] >= tr["premove_s"] else (None, alarm)
    if urged:
        return "urged", urged
    if tr["t_since_cue_s"] >= tr["premove_s"]:
        return "delay_over", alarm
    if n_moving >= tr["social_threshold"]:
        return "social", [p["id"] for p in moving]
    return None, alarm


def _record(view: dict, choice: str, why: str, evidence: list[str], go: bool) -> dict:
    return {
        "decision": {
            "padm": {
                "threat_real": "yes" if go else "unsure", "threat_evidence": evidence,
                "need_action": "yes" if go else "later", "need_evidence": evidence,
                "options_considered": [o["id"] for o in view["options"]],
                "choice": choice, "why": why, "choice_evidence": evidence,
                "when": "now" if go else "after_task",
            },
        },
        "validation": {"status": "ok", "errors": []},
        "raw_output": None, "latency_ms": 0, "cost_usd": 0.0,
    }


def _say(view: dict, opt: dict, reason: str, blocked: set[str]) -> str | None:
    if reason == "hazard":
        return "Fire! Get out!"
    if blocked:
        return f"Don't use {sorted(blocked)[0]}, it's blocked."
    if view["agent"]["persona"] == "staff" and opt.get("exit"):
        return f"Everyone out, use {opt.get('label') or opt['exit']}."
    return None


def _pick_exit(view: dict, blocked: set[str]) -> dict | None:
    a = view["agent"]
    speed = max(a["traits"]["speed_mps"], 0.1)
    evac = [o for o in view["options"] if o["action"] == "EVACUATE"]
    pool = [o for o in evac if o["exit"] not in blocked]

    def score(o: dict) -> float:
        bonus = FAMILIAR_BONUS_S if o["exit"] == a.get("entry_exit") and a["persona"] != "staff" else 0.0
        return o["route_m"] / speed + o.get("queue_s", 0.0) - bonus

    return min(pool, key=score) if pool else None


def decide(view: dict) -> dict:
    """Default occupant policy; pure function of `view`."""
    opts = {o["action"]: o for o in view["options"]}
    reason, evidence = _go_reason(view)
    if reason is None:
        act = "SEEK_INFO" if view["agent"]["persona"] in ("skeptic", "visitor") else "CONTINUE_TASK"
        choice = opts.get(act) or view["options"][0]
        return {"choice": choice["id"], "say": None,
                "record": _record(view, choice["id"], "Not sure it is real yet; waiting for more cues.", evidence, False)}
    blocked = _blocked_beliefs(view)
    follow = opts.get("FOLLOW")
    if reason == "social" and follow and follow.get("exit") not in blocked:
        return {"choice": follow["id"], "say": None,
                "record": _record(view, follow["id"], "Others around me are leaving, so I follow them.", evidence, True)}
    opt = _pick_exit(view, blocked) or follow or opts.get("SEEK_INFO") or view["options"][0]
    why = f"Leaving by {opt.get('exit', 'whatever way I can')} ({reason})."
    return {"choice": opt["id"], "say": _say(view, opt, reason, blocked) if opt.get("exit") else None,
            "record": _record(view, opt["id"], why, evidence, True)}


def baseline_decide(view: dict) -> dict:
    """Shortest open route to any exit, no delay, no beliefs."""
    evac = [o for o in view["options"] if o["action"] == "EVACUATE"]
    opt = min(evac, key=lambda o: o["route_m"]) if evac else view["options"][0]
    return {"choice": opt["id"], "say": None, "record": _record(view, opt["id"], "Shortest path.", [], True)}
