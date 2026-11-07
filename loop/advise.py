"""After-run advice: evidence (code) -> findings and recommendations (LLM) -> verified by re-running the sim.

    python -m loop.advise <run_dir> --scene S --scenario SC -o <run_dir>/recommendations.json \
        [--model anthropic/claude-opus-5.5] [--seeds 5] [--no-llm]

"LLM proposes, simulator verifies": every evidence item is computed here and gets an id (E1..En);
the model must cite them and may only reference scene ids; every recommendation that maps to a
patch (loop/patch.py interventions, plus add_exit and widen_door) is re-run with the rule policy
over N seeds against a fresh baseline on the same seeds. Times in s, widths in m.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import ssl
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable

import certifi
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from ingest_lidar.semantics import CACHE, _key

from .agent import tool
from .patch import _scene_errors, try_apply, validate_scenario
from .run_loop import _analyze
from .simrun import run_one

MODEL = "anthropic/claude-opus-5.5"
KINDS = ("add_exit", "widen_door", "refuge", "assisted_plan", "signage", "staffing", "pa_procedure", "layout", "other")
EFFORT = {"low": 0, "medium": 1, "high": 2}
MAX_FLOW = 1.3  # p/s/m, the sim's door flow (research/EVAC_PSYCHOLOGY.md 2, SFPE)
WIDTH_PER_OCC_M = 0.2 * 0.0254  # IBC 1005.3.2, 0.2 in per occupant [U] (research/MAPPING_IDEAS.md)
FLOW_WINDOW_S = 10.0
QUEUE_ZONE_M = 5.0
MAX_STRAGGLER_ITEMS = 10
PRINCIPLES = {
    "P1": "Door flow is capped near 1.3 persons/s per m of clear width (SFPE hydraulic model, research/EVAC_PSYCHOLOGY.md s2); more or wider exits raise capacity.",
    "P2": "Pre-movement is often the largest part of evacuation time; direct instructions from staff or a PA shorten it (BRANZ SR143, research/EVAC_PSYCHOLOGY.md s1).",
    "P3": "Offices with trained wardens start moving fast; wardens sweep areas and direct others (SR143, research/EVAC_PSYCHOLOGY.md s1 and s5).",
    "P4": "People who cannot use stairs wait at refuges unless a plan assigns helpers; only about 30% get help spontaneously (NIST NCSTAR 1-7, docs/SIM_INTERFACES.md).",
    "P5": "Occupants know few exits (the one they came in by, maybe the nearest); signs and drills add known exits (research/EVAC_PSYCHOLOGY.md s5 persona table).",
    "P6": "Hearsay about blocked exits spreads; an official message can correct it (research/EVAC_PSYCHOLOGY.md, sim rumor model).",
    "P7": "A room or door whose loss strands other rooms or removes exit width is a single point of failure (analysis/spof.py).",
    "P8": "Required safe egress time must stay below available safe egress time with margin (SFPE / ISO TR 16738 timeline: detection + alarm + pre-movement + travel).",
    "P9": "Egress width capacity of about 0.2 in (5 mm) per occupant (IBC 1005.3.2 as quoted in research/MAPPING_IDEAS.md, unverified).",
}
MOVING = ("moving", "queued", "helping", "carried")


# ---------------------------------------------------------------- evidence (code only)

def _med(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 1) if xs else None


def _p90(xs: list[float]) -> float | None:
    return round(sorted(xs)[int(0.9 * (len(xs) - 1))], 1) if xs else None


def _timeline(replay: dict) -> dict[str, dict]:
    """Per agent: first moving t, queued seconds, exit t and exit id, final frame row."""
    dt = float(replay["meta"].get("dt", 0.5))
    out: dict[str, dict] = {a["id"]: {"move_t": None, "queue_s": 0.0, "exit_t": None, "exit": None, "last": None}
                            for a in replay["agents"]}
    for f in replay["frames"]:
        for row in f["a"]:
            a = out[row[0]]
            if row[3] in MOVING and a["move_t"] is None:
                a["move_t"] = f["t"]
            if row[3] == "queued":
                a["queue_s"] += dt
            a["last"] = row
    for ev in replay["events"]:
        if ev["kind"] == "exited" and ev.get("agent") in out:
            out[ev["agent"]].update(exit_t=ev["t"], exit=ev.get("ref"))
    return out


def _room_index(scene: dict) -> dict[str, list[tuple[str, Polygon]]]:
    idx: dict[str, list] = defaultdict(list)
    for r in scene["rooms"]:
        idx[r["level"]].append((r["id"], Polygon(r["polygon"]).buffer(0)))
    return idx


def _room_at(idx: dict, level: str, x: float, z: float) -> str | None:
    p = Point(x, z)
    return next((rid for rid, poly in idx.get(level, []) if poly.covers(p)), None)


def exit_candidates(scene: dict) -> list[dict]:
    """Non-exit doors on a level's outer outline, on levels that already discharge outside.

    ponytail: geometric screen (door midpoint within 0.35 m of the outline); whether the far side
    is a safe place outside is for the architect to confirm."""
    ex_doors = {e["door_id"] for e in scene["exits"]}
    ex_levels = {e["level"] for e in scene["exits"]}
    out = []
    for lv in ex_levels:
        u = unary_union([Polygon(r["polygon"]).buffer(0.15) for r in scene["rooms"] if r["level"] == lv])
        rings = [g.exterior for g in getattr(u, "geoms", [u])]
        for d in scene["doors"]:
            if d["level"] == lv and d["id"] not in ex_doors and \
                    min(r.distance(LineString(d["segment"]).centroid) for r in rings) < 0.35:
                out.append({"door": d["id"], "level": lv, "width_m": d["width_m"], "connects": d["connects"]})
    return out


def build_evidence(run_dir: Path, scene: dict, scenario: dict, analysis: dict) -> tuple[list[dict], dict]:
    """[{id, text}] plus the raw numbers the heuristic and the scene summary use."""
    replay = json.loads((run_dir / "replay.json").read_text())
    recs = [json.loads(x) for x in (run_dir / "decisions.jsonl").read_text().splitlines() if x.strip()] \
        if (run_dir / "decisions.jsonl").exists() else []
    m = replay["metrics"]
    alarm = float(m.get("alarm_t_s", 0.0))
    persona = {a["id"]: a["persona"] for a in replay["agents"]}
    tl = _timeline(replay)
    items: list[str] = []
    data: dict = {"refuge": [], "straggler_rooms": Counter(), "unused_exits": [], "personas": Counter()}

    def rset(ids: list[str]) -> dict:
        pre = [tl[i]["move_t"] - alarm for i in ids if tl[i]["move_t"] is not None]
        out = [i for i in ids if tl[i]["exit_t"] is not None]
        travel = [tl[i]["exit_t"] - tl[i]["move_t"] - tl[i]["queue_s"] for i in out if tl[i]["move_t"] is not None]
        return {"n": len(ids), "out": len(out), "pre": pre, "travel": travel,
                "queue": [tl[i]["queue_s"] for i in out], "last": max((tl[i]["exit_t"] for i in out), default=None)}

    allr = rset(list(tl))
    items.append(f"RSET overall: alarm at {alarm:g} s; pre-movement median {_med(allr['pre'])} s (p90 {_p90(allr['pre'])}); "
                 f"travel median {_med(allr['travel'])} s; queueing median {_med(allr['queue'])} s (p90 {_p90(allr['queue'])}); "
                 f"last exit {allr['last']} s; evacuated {allr['out']}/{allr['n']} by {replay['meta'].get('t_end_s')} s.")
    by_p: dict[str, list[str]] = defaultdict(list)
    for i, p in persona.items():
        by_p[p].append(i)
    for p, ids in sorted(by_p.items()):
        r = rset(ids)
        data["personas"][p] = len(ids)
        items.append(f"RSET {p} ({r['n']} people, {r['out']} out): pre-movement median {_med(r['pre'])} s, "
                     f"travel median {_med(r['travel'])} s, queueing median {_med(r['queue'])} s, last exit {r['last']} s.")
    smoke = (analysis.get("smoke") or {}).get("rooms", {})
    for ev in scenario.get("events", []):
        room = (ev.get("at") or {}).get("room")
        if ev["type"] in ("fire", "smoke") and room in smoke:
            aset = smoke[room].get("t_head_height_s")
            margin = None if aset is None or allr["last"] is None else round(ev["t_start_s"] + aset - allr["last"], 1)
            items.append(f"ASET estimate: smoke from the {ev['type']} in {room} (t={ev['t_start_s']:g} s) reaches 1.8 m in that room "
                         f"after {aset} s (closed-room zone estimate, analysis/smoke.py, room of origin only). Against the last exit "
                         f"({allr['last']} s) the ASET-RSET margin is {margin} s" + (" (negative: not everyone is out in time)." if margin is not None and margin < 0 else "."))
    idx = _room_index(scene)
    last_rec = {}
    for r in recs:
        last_rec[r["agent_id"]] = r
    strag = [i for i in tl if tl[i]["exit_t"] is None]
    for i in strag:
        row = tl[i]["last"]
        room = _room_at(idx, row[4] if len(row) > 4 else scene["levels"][0]["id"], row[1], row[2])
        data["straggler_rooms"][room] += 1
        if row[3] == "waiting_at_refuge":
            data["refuge"].append(room)
    states = Counter(tl[i]["last"][3] for i in strag)
    items.append(f"Stragglers: {len(strag)} of {len(tl)} still inside at the end; by persona "
                 f"{dict(Counter(persona[i] for i in strag))}; by state {dict(states)}; by room {dict(data['straggler_rooms'].most_common(8))}.")
    for i in strag[:MAX_STRAGGLER_ITEMS]:
        row, rec = tl[i]["last"], last_rec.get(i) or {}
        padm = ((rec.get("decision") or {}).get("padm") or {})
        act = next((o["action"] for o in rec.get("options", []) if o["id"] == rec.get("choice")), None)
        lvl = row[4] if len(row) > 4 else "?"
        items.append(f"Straggler {i} ({persona[i]}): ends {row[3]} on {lvl} in room {_room_at(idx, lvl, row[1], row[2])}; last decision at "
                     f"t={rec.get('t_s')} s: {act}, evidence {padm.get('choice_evidence')}, why: {str(padm.get('why') or '')[:140]!r}.")
    widths = {e["id"]: e["width_m"] for e in scene["exits"]}
    exit_lv = {e["id"]: e["level"] for e in scene["exits"]}
    usage = m.get("exit_usage", {})
    total_out = sum(usage.values()) or 1
    times: dict[str, list[float]] = defaultdict(list)
    for ev in replay["events"]:
        if ev["kind"] == "exited":
            times[ev.get("ref")].append(ev["t"])
    qmax = _queues(replay, scene)
    for e in sorted(usage, key=lambda e: -usage[e]):
        ts = sorted(times.get(e, []))
        peak = max((sum(1 for t in ts if t0 <= t < t0 + FLOW_WINDOW_S) for t0 in ts), default=0) / FLOW_WINDOW_S
        if usage[e]:
            items.append(f"Exit {e} ({exit_lv.get(e)}, {widths.get(e)} m): {usage[e]} people ({usage[e] / total_out:.0%}); peak flow "
                         f"{peak / widths.get(e, 1):.2f} p/s/m over {FLOW_WINDOW_S:g} s against the sim's {MAX_FLOW} p/s/m maximum; "
                         f"max {qmax.get(e, 0)} queued within {QUEUE_ZONE_M:g} m.")
    data["unused_exits"] = [e for e in usage if not usage[e]]
    items.append(f"Unused exits ({len(data['unused_exits'])} of {len(usage)}): {data['unused_exits']}.")
    busy_stairs = sorted(((k, v) for k, v in qmax.items() if k not in usage and v), key=lambda kv: -kv[1])[:5]
    if busy_stairs:
        items.append(f"Stair queues (max people queued within {QUEUE_ZONE_M:g} m of a stair end): {dict(busy_stairs)}.")
    n_occ = sum(g["count"] for g in scenario.get("occupants", []))
    need, have = n_occ * WIDTH_PER_OCC_M, sum(widths.values())
    items.append(f"Screening, not a code review: egress width {have:.1f} m of exits for {n_occ} occupants; at 0.2 in per occupant "
                 f"(IBC 1005.3.2 as quoted in research/MAPPING_IDEAS.md, unverified) {need:.2f} m is needed: "
                 f"{'enough' if have >= need else 'short'}. Exits per level against occupant load: not checked (no factor in analysis/ or research/).")
    items.append(f"Screening, not a code review: accessible means of egress: {len(data['refuge'])} people end the run waiting at a "
                 f"stair refuge (rooms {dict(Counter(data['refuge']))}); no assisted evacuation plan is in the scenario"
                 if not any(it.get("type") == "assisted_evacuation_plan" for it in scenario.get("interventions", []))
                 else f"Screening: {len(data['refuge'])} people end at a stair refuge with the assisted evacuation plan in place.")
    for s in (analysis.get("spof") or {}).get("ranked", [])[:5]:
        items.append(f"Single point of failure ({s['kind']} {s['id']}): losing it strands {len(s['stranded_rooms'])} rooms "
                     f"({s['stranded_occupants']} occupants by IBC load) and removes {s['exit_width_lost_share']:.0%} of exit width (analysis/spof.py).")
    pinch = analysis.get("pinch") or {}
    if pinch.get("rooms"):
        narrow = sorted(pinch["rooms"], key=lambda r: r.get("pinch_width_m") or 9)[:3]
        items.append("Pinch points (narrowest route width per room, analysis/pinch.py): " +
                     "; ".join(f"{r['room']} {r['pinch_width_m']} m to {(r.get('target') or {}).get('id')}" for r in narrow) + ".")
    else:
        items.append(f"Pinch points: not computed ({pinch.get('skipped', 'no pinch analysis')}).")
    return [{"id": f"E{i + 1}", "text": t} for i, t in enumerate(items)], data


def _queues(replay: dict, scene: dict) -> dict[str, int]:
    """Max agents queued within QUEUE_ZONE_M of each exit and each stair end (same level), over frames."""
    pts = [(e["id"], e["level"], e["position"]) for e in scene["exits"]]
    for s in scene.get("stairs", []):
        pts += [(s["id"], s["from_level"], s["bottom"]), (s["id"], s["to_level"], s["top"])]
    out: dict[str, int] = defaultdict(int)
    for f in replay["frames"][::4]:  # every 2 s
        q = [r for r in f["a"] if r[3] == "queued"]
        if not q:
            continue
        for pid, lv, (x, z) in pts:
            n = sum(1 for r in q if (len(r) < 5 or r[4] == lv) and math.hypot(r[1] - x, r[2] - z) <= QUEUE_ZONE_M)
            out[pid] = max(out[pid], n)
    return dict(out)


def scene_summary(scene: dict, scenario: dict, data: dict, cands: list[dict]) -> dict:
    occ_rooms = {g["where"]["room"] for g in scenario.get("occupants", [])}
    ev_rooms = {(e.get("at") or {}).get("room") for e in scenario.get("events", [])}
    keep = occ_rooms | ev_rooms | set(data["straggler_rooms"]) | set(data["refuge"])
    return {
        "levels": [lv["id"] for lv in scene["levels"]],
        "exits": [{"id": e["id"], "level": e["level"], "width_m": e["width_m"], "door": e["door_id"]} for e in scene["exits"]],
        "stairs": [{"id": s["id"], "from": s["from_level"], "to": s["to_level"], "width_m": s["width_m"]}
                   for s in scene.get("stairs", [])],
        "rooms_in_play": [{"id": r["id"], "level": r["level"], "type": r["type"], "area_m2": round(r["area_m2"])}
                          for r in scene["rooms"] if r["id"] in keep],
        "room_types_count": dict(Counter(r["type"] for r in scene["rooms"])),
        "exterior_doors_not_exits": cands,
        "scenario_events": scenario.get("events", []),
    }


# ---------------------------------------------------------------- LLM: diagnose and propose

def call_llm(messages: list[dict], tools: list[dict], model: str) -> tuple[dict, float]:
    """One cached chat completion with tools and reasoning on (low effort); (message, cost, 0 when cached)."""
    body = {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto", "max_tokens": 12000,
            "reasoning": {"effort": "low"}, "usage": {"include": True}}
    raw = json.dumps(body, sort_keys=True).encode()
    hit = CACHE / f"advise_{hashlib.sha256(raw).hexdigest()}.json"
    if hit.exists():
        return json.loads(hit.read_text())["message"], 0.0
    key = _key()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set (env or repo .env)")
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", raw,
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(4):  # shared key: back off on 429
        try:
            with urllib.request.urlopen(req, timeout=300, context=ssl.create_default_context(cafile=certifi.where())) as r:
                resp = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))
    if "choices" not in resp:
        raise RuntimeError(f"OpenRouter error: {str(resp.get('error', resp))[:300]}")
    msg = {k: v for k, v in resp["choices"][0]["message"].items() if k in ("role", "content", "tool_calls") and v is not None}
    CACHE.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps({"model": model, "message": msg}))
    return msg, float(resp.get("usage", {}).get("cost") or 0.0)


_STR, _IDS = {"type": "string"}, {"type": "array", "items": {"type": "string"}}
TOOLS = [
    tool("submit_findings", "What went wrong, each finding citing evidence ids (E..) and scene ids.",
         {"summary": _STR, "findings": {"type": "array", "items": {"type": "object", "required": ["title", "detail", "refs", "evidence"],
                                                                   "properties": {"title": _STR, "detail": _STR, "refs": _IDS, "evidence": _IDS}}}},
         ["summary", "findings"]),
    tool("submit_recommendations", "What to change. kind from the list; refs are scene ids (never coordinates); basis is a principle id.",
         {"recommendations": {"type": "array", "items": {"type": "object",
                              "required": ["title", "kind", "audience", "refs", "evidence", "basis", "why", "expected_effect", "effort"],
                              "properties": {"title": _STR, "kind": {"type": "string", "enum": list(KINDS)},
                                             "audience": {"type": "string", "enum": ["operations", "design"]},
                                             "refs": _IDS, "evidence": _IDS, "basis": {"type": "string", "enum": list(PRINCIPLES)},
                                             "why": _STR, "expected_effect": _STR,
                                             "effort": {"type": "string", "enum": list(EFFORT)}}}}},
         ["recommendations"]),
]


def _system() -> str:
    return ("You advise a building's safety team after an evacuation simulation. You get an evidence pack (ids E1..En, "
            "all computed by code) and a scene summary. First call submit_findings: what went wrong, each finding citing "
            "evidence ids and scene ids. Then call submit_recommendations: up to 6 changes, operations (staffing, PA, plans, "
            "signage) or design (add_exit, widen_door, refuge, layout). Use only ids that appear in the inputs. add_exit must "
            "reference one door from exterior_doors_not_exits; widen_door a door or exit id; signage an exit id (and a room); "
            "staffing a room id. basis must be one principle id from: " +
            "; ".join(f"{k}: {v}" for k, v in PRINCIPLES.items()) +
            ". Do not cite anything else. The simulator will re-run each executable change and measure it, so be specific.")


def _check_items(items: list, kind: str, ids: set, ev_ids: set) -> tuple[list, list[str]]:
    ok, errs = [], []
    for i, it in enumerate(items or []):
        e = [f"{kind}[{i}].refs: unknown id {r!r}" for r in it.get("refs", []) if r not in ids]
        e += [f"{kind}[{i}].evidence: unknown id {x!r}" for x in it.get("evidence", []) if x not in ev_ids]
        if not it.get("evidence"):
            e.append(f"{kind}[{i}].evidence: cite at least one evidence id")
        if kind == "recommendations":
            if it.get("kind") not in KINDS:
                e.append(f"{kind}[{i}].kind: one of {KINDS}")
            if it.get("basis") not in PRINCIPLES:
                e.append(f"{kind}[{i}].basis: one of {list(PRINCIPLES)}")
        (errs.extend(e) if e else ok.append(it))
    return ok, errs


def diagnose_and_propose(evidence: list[dict], summary: dict, scene_ids: set, model: str,
                         llm: Callable = call_llm, max_turns: int = 4) -> dict:
    """Findings and recommendations with validated ids; one repair round, then invalid items are dropped."""
    ev_ids = {e["id"] for e in evidence}
    messages = [{"role": "system", "content": _system()},
                {"role": "user", "content": json.dumps({"evidence": evidence, "scene": summary}, separators=(",", ":"))}]
    got: dict = {"summary": "", "findings": None, "recommendations": None}
    tries: Counter = Counter()
    cost = 0.0
    for _ in range(max_turns):
        msg, c = llm(messages, TOOLS, model)
        cost += c
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            messages.append({"role": "user", "content": "Call submit_findings and submit_recommendations."})
            continue
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError as e:
                args, res = {}, {"ok": False, "errors": [f"bad JSON: {e}"]}
            key = "findings" if name == "submit_findings" else "recommendations"
            ok, errs = _check_items(args.get(key), key, scene_ids, ev_ids)
            tries[key] += 1
            if errs and tries[key] < 2:
                res = {"ok": False, "errors": errs, "note": "fix and call again; invalid items are dropped next time"}
            else:
                got[key] = ok
                if key == "findings":
                    got["summary"] = str(args.get("summary", ""))
                res = {"ok": True, "kept": len(ok), "dropped": len(errs)}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(res)})
        if got["findings"] is not None and got["recommendations"] is not None:
            break
    return {**got, "findings": got["findings"] or [], "recommendations": got["recommendations"] or [], "cost_usd": cost}


def heuristic(evidence: list[dict], data: dict, scene: dict, scenario: dict, cands: list[dict]) -> dict:
    """--no-llm: fixed rules over the same evidence (refuges, unused exits, stragglers, pre-movement)."""
    eid = lambda word: [e["id"] for e in evidence if word in e["text"]][:3] or [evidence[0]["id"]]  # noqa: E731
    recs = []
    if data["refuge"]:
        recs.append({"title": "Adopt an assisted evacuation plan", "kind": "assisted_plan", "audience": "operations", "refs": [],
                     "evidence": eid("refuge"), "basis": "P4", "why": f"{len(data['refuge'])} people waited at stair refuges.",
                     "expected_effect": "Staff carry them out.", "effort": "low"})
    room = next((r for r, _ in data["straggler_rooms"].most_common() if r), None)
    if room:
        recs.append({"title": f"Station wardens in {room}", "kind": "staffing", "audience": "operations", "refs": [room],
                     "evidence": eid("Stragglers"), "basis": "P3", "why": f"Most stragglers ended in {room}.",
                     "expected_effect": "Shorter pre-movement there.", "effort": "low"})
    if data["unused_exits"]:
        recs.append({"title": f"Sign exit {data['unused_exits'][0]}", "kind": "signage", "audience": "operations",
                     "refs": [data["unused_exits"][0]], "evidence": eid("Unused exits"), "basis": "P5",
                     "why": "Unused exit capacity.", "expected_effect": "Load spreads.", "effort": "low"})
    recs.append({"title": "PA announcement at the alarm", "kind": "pa_procedure", "audience": "operations", "refs": [],
                 "evidence": eid("RSET overall"), "basis": "P2", "why": "Pre-movement dominates.",
                 "expected_effect": "Earlier start.", "effort": "low"})
    if cands:
        recs.append({"title": f"Make door {cands[0]['door']} an exit", "kind": "add_exit", "audience": "design",
                     "refs": [cands[0]["door"]], "evidence": eid("Exit"), "basis": "P1", "why": "More exit width.",
                     "expected_effect": "Lower queues.", "effort": "high"})
    return {"summary": evidence[0]["text"], "findings": [{"title": "Run summary", "detail": e["text"], "refs": [], "evidence": [e["id"]]}
                                                          for e in evidence[:3]], "recommendations": recs, "cost_usd": 0.0}


# ---------------------------------------------------------------- verify (simulator, rule policy)

def to_patch(rec: dict, scene: dict, scenario: dict, cands: list[dict]) -> tuple[dict, dict] | None:
    """(scene, scenario) with the recommendation applied, or None when it is not executable."""
    refs, kind = rec.get("refs", []), rec["kind"]
    rooms = {r["id"] for r in scene["rooms"]}
    exits = {e["id"]: e for e in scene["exits"]}
    doors = {d["id"]: d for d in scene["doors"]}
    room = next((r for r in refs if r in rooms), None)
    ex = next((r for r in refs if r in exits), None)
    door = next((r for r in refs if r in doors), None) or (exits[ex]["door_id"] if ex else None)
    alarm = min([e["t_start_s"] for e in scenario.get("events", [])] or [0.0])
    iv = None
    if kind == "add_exit":
        cand = next((c for c in cands if c["door"] in refs), None)
        if cand is None:
            return None
        d = doors[cand["door"]]
        new = copy.deepcopy(scene)
        (x0, z0), (x1, z1) = d["segment"]
        new["exits"].append({"id": f"exit_new_{d['id']}", "level": d["level"], "door_id": d["id"],
                             "position": [round((x0 + x1) / 2, 3), round((z0 + z1) / 2, 3)], "width_m": d["width_m"],
                             "leads_to": "outside", "label": f"New exit at {d['id']}", "assembly_point": None})
        for nd in new["doors"]:
            if nd["id"] == d["id"]:
                nd["is_exit"] = True
        if _scene_errors(new) - _scene_errors(scene):
            return None
        return new, scenario
    if kind == "widen_door" and door:
        w = round(min(2.4, doors[door]["width_m"] * 1.5), 2)
        iv = {"kind": "widen_door", "args": {"door": door, "width_m": w}}
    elif kind == "signage" and ex:
        iv = {"kind": "add_signage", "args": {"room": room or scenario["occupants"][0]["where"]["room"], "exit": ex}}
    elif kind == "staffing" and room:
        iv = {"kind": "add_warden", "args": {"room": room, "count": 2}}
    elif kind == "pa_procedure":
        iv = {"kind": "pa_announcement", "args": {"t_s": alarm + 10, "text": "Fire alarm. Leave the building now by the nearest exit.",
                                                  **({"exit_open": ex} if ex else {})}}
    elif kind == "assisted_plan":
        iv = {"kind": "assisted_evacuation_plan", "args": {}}
    if iv is None:
        return None
    new_scene, new_sc, errs = try_apply({"id": "advise", "why": rec.get("why", ""), **iv}, scene, scenario)
    return None if errs else (new_scene, new_sc)


def _runs(scene: dict, sc: dict, seeds: list[int], d: Path, n0: int) -> list[dict]:
    jobs = [(scene, {**sc, "seed": s}, s, d / f"s{s}", "policy", True, n0) for s in seeds]
    with ProcessPoolExecutor(min(len(jobs), 8)) as ex:
        return list(ex.map(run_one, *zip(*jobs)))


def _stat(sums: list[dict]) -> dict:
    med = lambda k: (round(statistics.median([s[k] for s in sums if s[k] is not None]), 1)  # noqa: E731
                     if any(s[k] is not None for s in sums) else None)
    return {"evacuated": med("evacuated"), "total": sums[0]["total"], "t90_s": med("t90_s"), "t_last_s": med("t_last_s"),
            "stragglers": med("stragglers"), "max_queue": med("max_queue")}


def verify(recs: list[dict], scene: dict, scenario: dict, cands: list[dict], seeds: list[int], work: Path) -> list[dict]:
    """Attach verified {simulated, seeds, before, after} (or simulated false) and rank by measured effect, then effort."""
    n0 = sum(g["count"] for g in scenario["occupants"])
    before = _stat(_runs(scene, scenario, seeds, work / "baseline", n0))
    out = []
    for i, rec in enumerate(recs):
        p = to_patch(rec, scene, scenario, cands)
        if p is None:
            out.append({**rec, "verified": {"simulated": False, "seeds": 0, "before": None, "after": None}})
            continue
        after = _stat(_runs(p[0], p[1], seeds, work / f"rec{i + 1}", n0))
        out.append({**rec, "verified": {"simulated": True, "seeds": len(seeds), "before": before, "after": after}})

    def gain(r: dict) -> tuple:
        v = r["verified"]
        if not v["simulated"]:
            return (1, 0, 0, EFFORT.get(r.get("effort"), 1))
        b, a = v["before"], v["after"]
        return (0, (a["stragglers"] or 0) - (b["stragglers"] or 0), (a["t90_s"] or 1e9) - (b["t90_s"] or 1e9),
                EFFORT.get(r.get("effort"), 1))
    return sorted(out, key=gain)


# ---------------------------------------------------------------- report

def report_md(res: dict) -> str:
    def eff(r: dict) -> str:
        v = r["verified"]
        if not v or not v["simulated"]:
            return "not simulated"
        b, a = v["before"], v["after"]
        return (f"simulated over {v['seeds']} seeds (rule policy): evacuated {b['evacuated']:g} -> {a['evacuated']:g} of {b['total']}, "
                f"stragglers {b['stragglers']:g} -> {a['stragglers']:g}, t90 {b['t90_s']} -> {a['t90_s']} s, last exit {b['t_last_s']} -> {a['t_last_s']} s")
    lines = ["# What went wrong and what to change", "", res["summary"], "", "## Findings", ""]
    lines += [f"- **{f['title']}** ({', '.join(f['evidence'])}): {f['detail']}" for f in res["findings"]]
    for aud, title in (("operations", "For the safety team"), ("design", "For the architect")):
        lines += ["", f"## {title}", ""]
        lines += [f"{i}. **{r['title']}** ({r['kind']}, effort {r['effort']}; basis {r['basis']}; evidence {', '.join(r['evidence'])}). "
                  f"{r['why']} Expected: {r['expected_effect']} Measured: {eff(r)}."
                  for i, r in enumerate([r for r in res["recommendations"] if r["audience"] == aud], 1)] or ["None."]
    lines += ["", "## Evidence", ""] + [f"- {e['id']}: {e['text']}" for e in res["evidence"]]
    lines += ["", "Principles: " + " ".join(f"{k}: {v}" for k, v in PRINCIPLES.items()), "",
              f"Model {res['model']}; LLM cost ${res['cost_usd']:.4f}. Screening checks are not a code review; "
              "simulated effects use the rule policy, not LLM occupants.", ""]
    return "\n".join(lines)


def advise(run_dir: Path, scene_path: str, scenario_path: str, out: Path, model: str = MODEL, n_seeds: int = 5,
           no_llm: bool = False, llm: Callable = call_llm) -> dict:
    scene = json.loads(Path(scene_path).read_text())
    scenario = {**json.loads(Path(scenario_path).read_text()), "scene": scene_path}
    errs = validate_scenario(scenario, scene)
    if errs:
        raise ValueError("scenario invalid: " + "; ".join(errs))
    cached = run_dir / "analysis.json"
    analysis = json.loads(cached.read_text()) if cached.exists() else _analyze(scene)
    cached.write_text(json.dumps(analysis))
    evidence, data = build_evidence(run_dir, scene, scenario, analysis)
    cands = exit_candidates(scene)
    summary = scene_summary(scene, scenario, data, cands)
    ids = ({r["id"] for r in scene["rooms"]} | {d["id"] for d in scene["doors"]} | {e["id"] for e in scene["exits"]}
           | {s["id"] for s in scene.get("stairs", [])} | {lv["id"] for lv in scene["levels"]})
    res = heuristic(evidence, data, scene, scenario, cands) if no_llm else \
        diagnose_and_propose(evidence, summary, ids, model, llm)
    seeds = [42 + i for i in range(n_seeds)]
    recs = verify(res["recommendations"], scene, scenario, cands, seeds, out.parent / "advise_runs")
    body = {"model": "heuristic" if no_llm else model, "summary": res["summary"], "evidence": evidence,
            "findings": res["findings"], "recommendations": recs, "cost_usd": round(res["cost_usd"], 4)}
    out.write_text(json.dumps(body, indent=1))
    out.with_name("report.md").write_text(report_md(body))
    return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m loop.advise")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args(argv)
    out = a.out or a.run_dir / "recommendations.json"
    body = advise(a.run_dir, a.scene, a.scenario, out, a.model, a.seeds, a.no_llm)
    print(f"wrote {out} and {out.with_name('report.md')}: {len(body['findings'])} findings, "
          f"{len(body['recommendations'])} recommendations, ${body['cost_usd']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
