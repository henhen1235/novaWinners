"""Optimizer: reads the attacked run (metrics + decision-log digest) and picks fixes from a fixed menu.

Menu (loop/patch.py): add_warden, add_signage, pa_announcement, unlock_exit, assign_familiar_exit,
widen_door. Each tool call is checked and applied as a scene/scenario patch; errors go back to the
model for repair. --no-llm (or an LLM failure) uses a fixed heuristic over the same digest.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Callable

from scenario.chat import call_llm

from .adversary import building_brief
from .agent import MODEL, tool, tool_loop
from .patch import try_apply

MAX_INTERVENTIONS = 3
PA_AFTER_RUMOR_S = 10.0

_WHY = {"why": {"type": "string", "description": "What failure this fixes; cite numbers from the run digest"}}
_S = {"type": "string"}
TOOLS = [
    tool("add_warden", "Station 1-2 trained staff (wardens) in a room; they know every exit and direct people.",
         {"room": _S, "count": {"type": "integer", "minimum": 1, "maximum": 2}, **_WHY}, ["room", "why"]),
    tool("add_signage", "Add exit signs for an exit (occupants learn that exit with probability 0.8).",
         {"room": _S, "exit": _S, **_WHY}, ["room", "exit", "why"]),
    tool("pa_announcement", "Official PA message at time t_s (s); can state that an exit is open to counter a rumor.",
         {"t_s": {"type": "number", "minimum": 0}, "text": _S, "exit_open": _S, **_WHY}, ["t_s", "text", "why"]),
    tool("unlock_exit", "Give a key holder the job of opening a locked, chained or obstructed exit (open 30 s after it is blocked).",
         {"exit": _S, **_WHY}, ["exit", "why"]),
    tool("assign_familiar_exit", "Drill a room's occupants to use one exit (it becomes their familiar exit).",
         {"room": _S, "exit": _S, **_WHY}, ["room", "exit", "why"]),
    tool("widen_door", "Widen a door (at most 1.5x and 2.4 m); raises its flow.",
         {"door": _S, "width_m": {"type": "number"}, **_WHY}, ["door", "width_m", "why"]),
    tool("submit", "Finish this round's fixes.", {"summary": _S}, ["summary"]),
]


def digest(summaries: list[dict]) -> dict:
    """Decision-log digest over seeds: where people stalled, who believed the rumor, where queues formed."""
    def total(key: str) -> dict:
        c: Counter = Counter()
        for s in summaries:
            c.update(s.get(key) or {})
        return dict(c.most_common())
    return {"seeds": len(summaries), "stragglers_by_room": total("stragglers_by_room"),
            "stragglers_by_state": total("stragglers_by_state"), "stragglers_by_persona": total("stragglers_by_persona"),
            "misled_by_room": total("misled_by_room"),
            "max_people_near_exit": {e: max(s["max_near_exit"].get(e, 0) for s in summaries)
                                     for e in summaries[0]["max_near_exit"]} if summaries else {},
            "exit_by_room_first_seed": summaries[0].get("exit_by_room") if summaries else {},
            "decision_samples": summaries[0].get("why_samples", []) if summaries else []}


def _system(round_no: int) -> str:
    return f"""You are the safety engineer for an evacuation drill simulator (round {round_no}).
An adversary just injected failures (listed with their reasons). Pick up to {MAX_INTERVENTIONS} fixes from
the tools that cut the time to clear 95% of occupants, the stragglers and the people misled by rumors.
Rules: use only ids from the brief. No new geometry. Every `why` must cite numbers from the digest
(stragglers per room, misled count, people near an exit, exit usage). Tool results return
validation errors: fix them and call again. Call submit when done."""


class _Fix:
    def __init__(self, scene: dict, scenario: dict, round_no: int) -> None:
        self.scene, self.sc, self.round = scene, scenario, round_no
        self.interventions: list[dict] = []

    def __call__(self, name: str, a: dict) -> tuple[dict, bool]:
        if name == "submit":
            return {"ok": True}, True
        if len(self.interventions) >= MAX_INTERVENTIONS:
            return {"ok": False, "errors": [f"at most {MAX_INTERVENTIONS} fixes; call submit"]}, True
        iv = {"id": f"fix{self.round}_{len(self.interventions) + 1}", "kind": name,
              "args": {k: v for k, v in a.items() if k != "why"}, "why": str(a.get("why", ""))}
        self.scene, self.sc, errs = try_apply(iv, self.scene, self.sc)
        if errs:
            return {"ok": False, "errors": errs}, False
        self.interventions.append(iv)
        return {"ok": True, "id": iv["id"], "applied": len(self.interventions)}, len(self.interventions) >= MAX_INTERVENTIONS


def heuristic_calls(scene: dict, scenario: dict, dig: dict, metrics: dict) -> list[tuple[str, dict]]:
    """Unlock what staff can unlock, counter each rumor by PA, put a warden where most people stalled."""
    out: list[tuple[str, dict]] = []
    opened = {e["at"].get("exit") for e in scenario.get("events", []) if e["type"] == "open_exit"}
    blocked = {e["at"]["exit"] for e in scenario.get("events", []) if e["type"] == "blocked_exit"}
    for e in scenario.get("events", []):
        if e["type"] == "blocked_exit" and e["at"]["exit"] not in opened:
            out.append(("unlock_exit", {"exit": e["at"]["exit"],
                                        "why": f"{e['at']['exit']} is {e['params'].get('cause', 'locked')}; "
                                               f"{metrics.get('stragglers', 0):g} stragglers in the median run."}))
    misled = sum(dig.get("misled_by_room", {}).values())
    labels = {x["id"]: x["label"] for x in scene["exits"]}
    countered = {(e.get("params") or {}).get("claim", {}).get("exit_open") for e in scenario.get("events", [])
                 if e["type"] == "pa_announcement"}
    for e in sorted(scenario.get("events", []), key=lambda e: e["t_start_s"]):
        claim = (e.get("params") or {}).get("claim", {}).get("exit_blocked")
        if e["type"] == "rumor" and claim and claim not in blocked | countered and misled:
            countered.add(claim)
            out.append(("pa_announcement", {"t_s": e["t_start_s"] + PA_AFTER_RUMOR_S, "exit_open": claim,
                                            "text": f"Building announcement: {labels[claim]} is open. Leave now by the nearest exit.",
                                            "why": f"{misled} occupants over {dig['seeds']} seeds believed the rumor that {claim} is blocked."}))
    strag = dig.get("stragglers_by_room") or {}
    room = next((r for r in strag if r), None)
    if room is None:
        occ = Counter()
        for g in scenario.get("occupants", []):
            occ[g["where"]["room"]] += g["count"]
        room = occ.most_common(1)[0][0]
    out.append(("add_warden", {"room": room, "count": 1,
                               "why": f"{strag.get(room, 0)} stragglers over {dig['seeds']} seeds started in {room}."}))
    return out[:MAX_INTERVENTIONS]


def fix(scene: dict, scenario: dict, analysis: dict, summaries: list[dict], metrics: dict, injections: list[dict],
        round_no: int = 1, no_llm: bool = False, llm: Callable = call_llm, model: str = MODEL) -> dict:
    """Returns {scene, scenario, interventions, digest, source, cost_usd, llm_calls, messages}."""
    dig = digest(summaries)
    h = _Fix(scene, scenario, round_no)
    res = {"messages": [], "cost_usd": 0.0, "llm_calls": 0}
    source = "heuristic"
    if not no_llm:
        user = json.dumps({"brief": json.loads(building_brief(scene, analysis, scenario, metrics)),
                           "adversary_injections": [{"event": i["event"], "why": i["why"]} for i in injections],
                           "attacked_run_digest": dig}, separators=(",", ":"))
        try:
            res = tool_loop(_system(round_no), user, TOOLS, h, llm, model)
            source = "llm"
        except (RuntimeError, OSError) as e:
            res["messages"] = [{"role": "error", "content": f"{type(e).__name__}: {e}"}]
    if not h.interventions:
        source = "heuristic" if source == "heuristic" else "heuristic (llm gave no valid fix)"
        for name, args in heuristic_calls(h.scene, h.sc, dig, metrics):
            h(name, args)
    return {"scene": h.scene, "scenario": h.sc, "interventions": h.interventions, "digest": dig, "source": source, **res}
