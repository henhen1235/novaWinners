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

FAMILIAR_BONUS_S = 15.0
SKEPTIC_PEOPLE_LEAVING = 2
MOVING = ("EVACUATE", "FOLLOW", "TAKE_STAIRS", "GO_TO_REFUGE", "HELP")  # how much longer a route to the entry exit may be and still win


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
    official = [p["id"] for p in per if p["kind"] == "message" and p.get("official")]
    if hazard:
        return "hazard", hazard
    if a.get("carrying") or tr.get("activity") in MOVING:
        return "already_leaving", alarm
    if official:
        return "official", official
    needs = [p["id"] for p in per if p["kind"] == "needs_help"]
    if needs and a["persona"] == "staff":
        return "help_needed", needs
    waited = tr["t_since_cue_s"] or 0.0
    # Escalating pressure: the alarm keeps sounding and the building empties, so even the
    # long tail of the pre-movement distribution moves by the persona's p95 (capped at 5 min).
    delay = min(tr["premove_s"], tr.get("pressure_timeout_s") or tr["premove_s"])
    if a["persona"] == "skeptic":  # the alarm alone is not enough; any second cue is
        if urged:
            return "second_cue", urged
        if n_moving >= SKEPTIC_PEOPLE_LEAVING:
            return "second_cue", [p["id"] for p in moving]
        return ("delay_over", alarm) if waited >= delay else (None, alarm)
    if urged:
        return "urged", urged
    if waited >= delay:
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


def _eta(o: dict, speed: float) -> float:
    return o.get("eta_s", o.get("route_m", 0.0) / speed + o.get("queue_s", 0.0))


def _pick_exit(view: dict, blocked: set[str]) -> dict | None:
    a = view["agent"]
    speed = max(a["traits"]["speed_mps"], 0.1)
    evac = [o for o in view["options"] if o["action"] == "EVACUATE"]
    pool = [o for o in evac if o["exit"] not in blocked]

    def score(o: dict) -> float:
        bonus = FAMILIAR_BONUS_S if o["exit"] == a.get("entry_exit") and a["persona"] != "staff" else 0.0
        hazard_s = 60.0 if o.get("through_hazard") else 0.0
        return _eta(o, speed) + hazard_s - bonus

    return min(pool, key=score) if pool else None


def _pick_stair(view: dict) -> dict | None:
    """Best open stair: down before up, then shortest eta; smoky stairs cost an extra minute."""
    speed = max(view["agent"]["traits"]["speed_mps"], 0.1)
    pool = [o for o in view["options"] if o["action"] == "TAKE_STAIRS" and not o.get("blocked")
            and o.get("direction") != "up"]  # going up only moves people away from the exits
    return min(pool, key=lambda o: (o.get("direction") != "down", _eta(o, speed) + (60 if o.get("smoke") else 0)),
               default=None)


def _nearest(view: dict, action: str) -> dict | None:
    return min((o for o in view["options"] if o["action"] == action), key=lambda o: o.get("route_m", 0), default=None)


def _pace(view: dict, reason: str) -> str:
    if reason == "hazard" or view["agent"].get("carrying"):
        return "hurry" if reason == "hazard" else "brisk"
    return "calm" if view["agent"]["persona"] == "skeptic" else "brisk"


def _result(view: dict, opt: dict, why: str, evidence: list[str], go: bool, say: str | None, pace: str) -> dict:
    rec = _record(view, opt["id"], why, evidence, go)
    rec["decision"]["pace"] = pace
    return {"choice": opt["id"], "say": say, "pace": pace, "record": rec}


def decide(view: dict) -> dict:
    """Default occupant policy; pure function of `view`."""
    opts = {o["action"]: o for o in view["options"]}
    reason, evidence = _go_reason(view)
    if reason is None:
        act = "SEEK_INFO" if view["agent"]["persona"] in ("skeptic", "visitor") else "CONTINUE_TASK"
        choice = opts.get(act) or view["options"][0]
        return _result(view, choice, "Not sure it is real yet; waiting for more cues.", evidence, False, None, "calm")
    pace = _pace(view, reason)
    help_opt = _nearest(view, "HELP")
    if help_opt and view["agent"]["persona"] == "staff":
        return _result(view, help_opt, f"{help_opt['target']} cannot use the stairs; I go and help.", evidence, True,
                       "I'm coming to help you.", pace)
    blocked = _blocked_beliefs(view)
    follow = opts.get("FOLLOW")
    if reason == "social" and follow and follow.get("exit") not in blocked:
        return _result(view, follow, "Others around me are leaving, so I follow them.", evidence, True, None, pace)
    if follow and follow.get("exit") in blocked:
        follow = None
    opt = _pick_exit(view, blocked) or _pick_stair(view)
    if opt is None and _nearest(view, "GO_TO_REFUGE"):
        opt = _nearest(view, "GO_TO_REFUGE")
        return _result(view, opt, "I cannot take the stairs; I wait at the landing for help.", evidence, True,
                       "I need help on the stairs!", "brisk")
    opt = opt or follow or opts.get("SEEK_INFO") or view["options"][0]
    why = f"{opt['action']} {opt.get('exit') or opt.get('stair') or ''} ({reason}).".replace("  ", " ")
    say = _say(view, opt, reason, blocked) if opt.get("exit") else None
    return _result(view, opt, why, evidence, True, say, pace)


def baseline_decide(view: dict) -> dict:
    """Shortest open route to any exit, no delay, no beliefs."""
    evac = [o for o in view["options"] if o["action"] == "EVACUATE"]
    opt = min(evac, key=lambda o: o["route_m"]) if evac else view["options"][0]
    return _result(view, opt, "Shortest path.", [], True, None, "brisk")
