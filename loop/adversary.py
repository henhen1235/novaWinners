"""Adversary: reads the scene, analysis.json and the current run metrics, injects 1-3 failures.

Each injection is a scenario event made by a tool call, validated (loop.patch.validate_scenario)
and returned with errors for repair. Every choice must cite a number from the analysis or the
metrics. --no-llm (or an LLM failure) uses a fixed heuristic over the same numbers.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Callable

from scenario.chat import call_llm

from .agent import MODEL, tool, tool_loop
from .patch import validate_scenario

MAX_INJECTIONS = 3
CAUSES = ["locked", "chained", "obstructed"]
RUMOR_T_S = 30.0

_WHY = {"why": {"type": "string", "description": "Why this hurts most; cite numbers from the analysis or metrics"}}
TOOLS = [
    tool("block_exit", "Make an exit impassable from t_start_s (a locked, chained or obstructed exit door).",
         {"exit": {"type": "string"}, "t_start_s": {"type": "number", "minimum": 0},
          "cause": {"type": "string", "enum": CAUSES}, **_WHY}, ["exit", "t_start_s", "cause", "why"]),
    tool("start_fire", "Start a fire or smoke in a room; it and its doors become impassable after block_after_s.",
         {"room": {"type": "string"}, "t_start_s": {"type": "number", "minimum": 0},
          "kind": {"type": "string", "enum": ["fire", "smoke"]}, "block_after_s": {"type": "number", "minimum": 10},
          **_WHY}, ["room", "t_start_s", "kind", "why"]),
    tool("spread_rumor", "Plant a false rumor in a room claiming an exit is blocked (people nearby hear it).",
         {"room": {"type": "string"}, "t_start_s": {"type": "number", "minimum": 0}, "text": {"type": "string"},
          "claim_exit_blocked": {"type": "string"}, **_WHY},
         ["room", "t_start_s", "text", "claim_exit_blocked", "why"]),
    tool("submit", "Finish the attack.", {"summary": {"type": "string"}}, ["summary"]),
]


def building_brief(scene: dict, analysis: dict, scenario: dict, metrics: dict) -> str:
    """Compact JSON the agents read: building, analysis highlights, scenario, aggregated run metrics."""
    occ: dict[str, int] = {}
    for g in scenario.get("occupants", []):
        occ[g["where"]["room"]] = occ.get(g["where"]["room"], 0) + g["count"]
    pinch = sorted(analysis.get("pinch", {}).get("rooms", []), key=lambda r: r.get("pinch_width_m") or 9)[:8]
    return json.dumps({
        "exits": [{"id": e["id"], "label": e["label"], "width_m": e["width_m"], "door": e["door_id"]}
                  for e in scene["exits"]],
        "rooms": [{"id": r["id"], "name": r["name"], "type": r["type"], "area_m2": round(r["area_m2"], 1),
                   "level": r["level"], "occupants": occ.get(r["id"], 0)} for r in scene["rooms"] if r["type"] != "outside"],
        "doors": [{"id": d["id"], "width_m": d["width_m"], "connects": d["connects"]} for d in scene["doors"]],
        "single_points_of_failure": [{k: s[k] for k in ("kind", "id", "stranded_occupants", "exit_width_lost_share")}
                                     | {"stranded_rooms": len(s["stranded_rooms"])}
                                     for s in analysis.get("spof", {}).get("ranked", [])[:6]],
        "pinch_points": [{k: p.get(k) for k in ("room", "pinch_width_m", "nearest_route_m", "flow_p_per_min")}
                         | {"exit": (p.get("target") or {}).get("id")} for p in pinch],
        "smoke_head_height_s": {r: v.get("t_head_height_s") for r, v in analysis.get("smoke", {}).get("rooms", {}).items()},
        "scenario_events": scenario.get("events", []),
        "run_metrics_median_over_seeds": metrics,
    }, separators=(",", ":"))


def _system(round_no: int) -> str:
    return f"""You are the red team for an evacuation drill simulator (round {round_no}).
Find the 1-{MAX_INJECTIONS} failures that hurt this building's evacuation the most: a blocked exit,
a fire or smoke in a room whose loss strands people, a false rumor that an exit is blocked.
Rules: use only ids from the brief. Every `why` must cite numbers from the analysis or the metrics
(exit usage, stranded occupants, exit width share, smoke time, pinch width). At least one exit must stay
open. Tool results return validation errors: fix them and call again. Call submit when done."""


class _Attack:
    """Tool handler: accumulates validated events onto a copy of the scenario."""

    def __init__(self, scene: dict, scenario: dict, round_no: int) -> None:
        self.scene, self.sc, self.round = scene, copy.deepcopy(scenario), round_no
        self.injections: list[dict] = []

    def _event(self, name: str, a: dict) -> dict:
        eid = f"adv{self.round}_{len(self.injections) + 1}"
        t = float(a.get("t_start_s", 0))
        if name == "block_exit":
            return {"id": eid, "type": "blocked_exit", "t_start_s": t, "at": {"exit": a["exit"]},
                    "params": {"cause": a.get("cause", "locked")}}
        if name == "start_fire":
            return {"id": eid, "type": a.get("kind", "fire"), "t_start_s": t, "at": {"room": a["room"]},
                    "params": {"block_after_s": float(a.get("block_after_s", 60))}}
        return {"id": eid, "type": "rumor", "t_start_s": t, "at": {"room": a["room"]},
                "params": {"text": a["text"], "claim": {"exit_blocked": a["claim_exit_blocked"]}}}

    def __call__(self, name: str, a: dict) -> tuple[dict, bool]:
        if name == "submit":
            return ({"ok": True}, True) if self.injections else ({"ok": False, "errors": ["inject at least one failure first"]}, False)
        if name not in ("block_exit", "start_fire", "spread_rumor"):
            return {"ok": False, "errors": [f"unknown tool {name}"]}, False
        if len(self.injections) >= MAX_INJECTIONS:
            return {"ok": False, "errors": [f"at most {MAX_INJECTIONS} injections; call submit"]}, True
        if not re.search(r"\d", str(a.get("why", ""))):
            return {"ok": False, "errors": ["why: cite at least one number from the analysis or metrics"]}, False
        ev = self._event(name, a)
        cand = {**self.sc, "events": self.sc.get("events", []) + [ev]}
        errs = validate_scenario(cand, self.scene)
        blocked = {e["at"].get("exit") for e in cand["events"] if e["type"] == "blocked_exit"}
        if len(blocked) >= len(self.scene["exits"]):
            errs.append("at least one exit must stay open")
        if errs:
            return {"ok": False, "errors": errs}, False
        self.sc = cand
        self.injections.append({"event": ev, "tool": name, "why": a["why"]})
        return {"ok": True, "event_id": ev["id"], "injected": len(self.injections)}, len(self.injections) >= MAX_INJECTIONS


def heuristic_calls(scene: dict, analysis: dict, scenario: dict, metrics: dict, round_no: int) -> list[tuple[str, dict]]:
    """Fixed attack over the same numbers: busiest exit, rumor on the next one, fire where loss strands most."""
    usage = metrics.get("exit_usage") or {}
    n = metrics.get("total") or 1
    blocked = {e["at"].get("exit") for e in scenario.get("events", []) if e["type"] == "blocked_exit"}
    open_ = sorted((e["id"] for e in scene["exits"] if e["id"] not in blocked), key=lambda e: -usage.get(e, 0))
    door_share = {s["id"]: s["exit_width_lost_share"] for s in analysis.get("spof", {}).get("ranked", [])}
    exit_door = {e["id"]: e["door_id"] for e in scene["exits"]}
    occ: dict[str, int] = {}
    for g in scenario.get("occupants", []):
        occ[g["where"]["room"]] = occ.get(g["where"]["room"], 0) + g["count"]
    busiest_room = max(occ, key=occ.get) if occ else scene["rooms"][0]["id"]
    out: list[tuple[str, dict]] = []
    if len(open_) >= 2:
        e = open_[0]
        out.append(("block_exit", {"exit": e, "t_start_s": 0, "cause": "locked",
                                   "why": f"{e} carried {usage.get(e, 0)} of {n} occupants in the last run (median); "
                                          f"losing door {exit_door[e]} removes {door_share.get(exit_door[e], 0):.0%} of exit width."}))
        e2 = open_[1]
        out.append(("spread_rumor", {"room": busiest_room, "t_start_s": RUMOR_T_S, "claim_exit_blocked": e2,
                                     "text": f"Someone said {next(x['label'] for x in scene['exits'] if x['id'] == e2)} is blocked too!",
                                     "why": f"{e2} is the remaining exit ({usage.get(e2, 0)} of {n} used it); the rumor starts in "
                                            f"{busiest_room}, which holds {occ.get(busiest_room, 0)} people."}))
    rooms = [s for s in analysis.get("spof", {}).get("ranked", []) if s["kind"] == "room" and s["id"] in occ]
    target = max(rooms, key=lambda s: s["stranded_occupants"])["id"] if rooms else busiest_room
    smoke = analysis.get("smoke", {}).get("rooms", {}).get(target, {}).get("t_head_height_s")
    out.append(("start_fire", {"room": target, "t_start_s": 0, "kind": "fire", "block_after_s": 60,
                               "why": f"{target} holds {occ.get(target, 0)} people; smoke reaches head height in "
                                      f"{smoke} s (analysis estimate), so its doors close early."}))
    return out[:2] if round_no == 1 else out[2:] or out[:1]


def attack(scene: dict, analysis: dict, scenario: dict, metrics: dict, round_no: int = 1,
           no_llm: bool = False, llm: Callable = call_llm, model: str = MODEL) -> dict:
    """Returns {scenario, injections, source, cost_usd, llm_calls, messages}."""
    h = _Attack(scene, scenario, round_no)
    res = {"messages": [], "cost_usd": 0.0, "llm_calls": 0}
    source = "heuristic"
    if not no_llm:
        try:
            res = tool_loop(_system(round_no), building_brief(scene, analysis, scenario, metrics), TOOLS, h, llm, model)
            source = "llm"
        except (RuntimeError, OSError) as e:  # no key, HTTP error: fall back, say so in the report
            res["messages"] = [{"role": "error", "content": f"{type(e).__name__}: {e}"}]
    if not h.injections:
        source = "heuristic" if no_llm or source == "heuristic" else "heuristic (llm gave no valid injection)"
        for name, args in heuristic_calls(scene, analysis, h.sc, metrics, round_no):
            h(name, args)
    return {"scenario": h.sc, "injections": h.injections, "source": source, **res}
