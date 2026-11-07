"""Hybrid occupants: routine decisions follow the person's playbook in code, surprises go to the LLM.

    python -m sim run --scenario <out>/scenario_hybrid.json -o <out> --decide cognition.hybrid

Routine triggers (periodic, door_reached, level_reached, premove_done, the alarm itself, group and
sweep bookkeeping) use the sim's persona rule driven by this person's playbook traits, unless
something changed since their last LLM call: their route or exit is blocked or smoky, a new cue
arrived (fire, smoke, an urgent or official message, a blocked exit), or they lost their group.
Surprises (hazard_seen, rumor_heard, path_blocked, stair_smoke, elevator_recalled, help_request,
waiting at a refuge, and heard when the speech is news: staff/PA, a blocked-exit claim, or the first
report of a hazard) get one LLM deliberation as in cognition.decide. Routine
records keep the DecisionRecord format with source "playbook".

The alarm is deliberately routine: the playbook was written for exactly that cue, and calling the
LLM for every person who notices it is the alarm-time burst this mode exists to avoid.
NOVA_COST_CAP (USD, default 1.0) stops new LLM calls once this process has spent that much; later
surprises are then decided by the playbook and logged with an error saying so.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

from . import llm
from .appraisal import parse_claims
from .decide import (CONFIG, MINDS, REPLAY, _ENV_DONE, _record, _remember, _result, build_inputs, compute_state,
                     configure, deliberate)

MODEL = "google/gemini-2.5-flash-lite"
ROUTINE = {"periodic", "door_reached", "level_reached", "premove_done", "group_moving", "sweep_next",
           "reached_person", "picked_up", "route_changed"}
SURPRISE = {"hazard_seen", "rumor_heard", "path_blocked", "stair_smoke", "elevator_recalled", "help_request"}
CUE_KINDS = {"fire", "smoke", "exit_blocked", "blocked", "intruder", "gunshot"}
STATS = {"playbook": 0, "llm": 0, "capped": 0, "replay": 0, "why": {}}


def _cost_cap() -> float:
    return float(os.environ.get("NOVA_COST_CAP", "1.0"))


def cue_signature(view: dict) -> list[str]:
    """Cues that would make a person rethink: hazards, blocked exits, urgent or official speech."""
    out = set()
    for p in view.get("percepts") or []:
        if p["kind"] in CUE_KINDS or (p["kind"] == "stair" and (p.get("smoke") or p.get("blocked"))):
            out.add(f"{p['kind']}:{p.get('ref') or p.get('where')}")
        elif p["kind"] == "message" and _news(p, view):
            out.add(f"message:{' '.join(str(p.get('text') or '').lower().split())[:60]}")  # same words, one cue
    for b in view.get("beliefs") or []:
        if b["key"].startswith(("exit_open:", "stair_ok:")) and b["value"] is False:
            out.add(b["key"])
    return sorted(out)


def _news(p: dict, view: dict) -> bool:
    """Speech worth an LLM call: staff or PA, a blocked-exit claim, or the first word of a hazard.
    Plain "let's go" chatter is not (in Tepper v1 it drove 1034 of 2191 calls)."""
    if p.get("official"):
        return True
    claims = parse_claims(p.get("text", ""))
    if claims.get("exit_blocked"):
        return True
    knows = any(b["key"].startswith("hazard:") and b["value"] for b in view.get("beliefs") or []) or any(
        q["kind"] in ("fire", "smoke") for q in view.get("percepts") or [])
    return bool(claims.get("hazard")) and not knows


def route_in_trouble(view: dict) -> str | None:
    a = view["agent"]
    blocked = {b["key"].split(":", 1)[1] for b in view.get("beliefs") or []
               if b["key"].startswith("exit_open:") and b["value"] is False}
    if a.get("target_exit") and a["target_exit"] in blocked:
        return f"target exit {a['target_exit']} believed blocked"
    for o in view.get("options") or []:
        if a.get("target_stair") and o.get("stair") == a["target_stair"] and (o.get("smoke") or o.get("blocked")):
            return f"target stair {a['target_stair']} smoky or blocked"
        if a.get("target_exit") and o.get("exit") == a["target_exit"] and (o.get("through_hazard") or o.get("blocked")):
            return f"route to {a['target_exit']} passes the hazard"
    return None


def escalate(view: dict) -> str | None:
    """Why this decision needs the LLM, or None for a routine one."""
    trig = (view.get("trigger") or {}).get("kind", "periodic")
    traits = view["agent"].get("traits") or {}
    if trig in SURPRISE:
        return trig
    if trig == "heard" and any(p["kind"] == "message" and _news(p, view) for p in view.get("percepts") or []):
        return "heard"
    if trig == "cue_noticed" and (view.get("trigger") or {}).get("ref") != "alarm":
        return "cue_noticed"
    if traits.get("state") == "waiting_at_refuge":
        return "waiting_at_refuge"
    mind = MINDS.get(view["agent"]["id"], {})
    if (why := route_in_trouble(view)) is not None:
        return why
    new = set(cue_signature(view)) - set(mind.get("cues_at_llm", []))
    if new:
        return f"new cue {sorted(new)[0]}"
    grouped = any(p["kind"] == "group_member" for p in view.get("percepts") or [])
    if mind.get("had_group") and not grouped:
        return "separated from group"
    return None


def _routine(view: dict, inp: dict, st: dict) -> dict:
    """The sim's persona rule, driven by the playbook traits, wrapped as a cognition result."""
    from sim.policy import decide as rule  # the calibrated rule the rule-only runs used
    r = rule(view)
    pb = (view["agent"].get("traits") or {}).get("playbook") or {}
    padm = dict(r["record"]["decision"]["padm"])
    plan = (f" Plan: {pb.get('first_action')}, convinced by {pb.get('convinced_by')}, "
            f"{pb.get('exit_preference')} exit." if pb else "")
    padm["why"] = (padm.get("why") or "")[:200] + plan
    say = r.get("say")
    moving = (view["agent"].get("traits") or {}).get("activity") in ("EVACUATE", "FOLLOW", "TAKE_STAIRS")
    if say is None and pb.get("helps_others") and pb.get("lines") and padm.get("when") == "now" and not moving:
        say = pb["lines"][0]  # people who warn others say their own line as they set off
    msgs = json.dumps({"rule": "sim.policy", "view_tick": view["tick"], "agent": view["agent"]["id"]})
    return {"prompt_sha256": hashlib.sha256(msgs.encode()).hexdigest(), "raw_output": "",
            "decision": {"padm": padm, "say": say, "feel": "calm", "confidence": 0.6, "pace": r.get("pace")},
            "validation": {"status": "ok", "errors": []}, "source": "playbook", "latency_ms": 0, "cost_usd": 0.0,
            "pace": r.get("pace")}


def decide_batch(views: list[dict]) -> list[dict]:
    if not _ENV_DONE[0]:
        configure(reset=False)
    CONFIG["model"] = MODEL
    out: list[dict | None] = [None] * len(views)
    jobs = []
    for i, v in enumerate(views):
        hit = REPLAY.get((int(v["tick"]), v["agent"]["id"]))
        if hit:
            STATS["replay"] += 1
            out[i] = {**_result(hit), "pace": hit.get("pace") or (hit.get("decision") or {}).get("pace")}
            continue
        inp = build_inputs(v)
        st = compute_state(inp)
        why = escalate(v)
        if why and not CONFIG["offline"] and llm.STATS["cost_usd"] >= _cost_cap():
            STATS["capped"] += 1
            res = _routine(v, inp, st)
            res["validation"]["errors"] = [f"cost cap {_cost_cap()} USD reached; wanted LLM for: {why}"]
            out[i] = {**_result(_record(inp, st, res)), "pace": res["pace"]}
        elif why:
            STATS["why"][why.split(" ")[0]] = STATS["why"].get(why.split(" ")[0], 0) + 1
            jobs.append((i, inp, st, why, v))
        else:
            STATS["playbook"] += 1
            res = _routine(v, inp, st)
            out[i] = {**_result(_record(inp, st, res)), "pace": res["pace"]}
    if jobs:
        with ThreadPoolExecutor(max_workers=min(CONFIG["workers"], len(jobs))) as ex:
            results = list(ex.map(lambda j: deliberate(j[1], j[2]), jobs))
        for (i, inp, st, why, v), res in zip(jobs, results):
            STATS["llm"] += 1
            out[i] = _result(_record(inp, st, {**res, "escalation": why}))
    for r, v in zip(out, views):
        aid = v["agent"]["id"]
        prev = MINDS.get(aid, {})
        _remember(r["record"])  # rebuilds MINDS[aid]; carry the hybrid bookkeeping across
        m = MINDS[aid]
        m["had_group"] = any(p["kind"] == "group_member" for p in v.get("percepts") or [])
        asked = r["record"].get("source") == "llm" or bool(r["record"].get("escalation"))
        m["cues_at_llm"] = cue_signature(v) if asked else prev.get("cues_at_llm", [])
    return out


def decide(view: dict) -> dict:
    return decide_batch([view])[0]


def stats() -> dict:
    from .decide import stats as llm_stats
    return {**STATS, **llm_stats()}
