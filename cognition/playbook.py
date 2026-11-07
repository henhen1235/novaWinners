"""Per-person playbooks written before the run (hybrid occupants).

    python -m cognition.playbook --scenario sim/examples/tepper_weekday_fire.json -o out/hybrid/tepper_weekday

One LLM pass before the run, about 20 people per call, at most 24 calls in flight, writes
<out>/playbooks.jsonl and <out>/scenario_hybrid.json: the scenario with one occupant entry per
person (count 1, same room as the group) whose traits carry the clamped playbook mapped onto the
sim's own traits (premove_s, social_threshold, sign_follow_p, exit_familiarity). Re-running with an
existing playbooks.jsonl for the same scenario reuses it without calls (replay).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
from pathlib import Path

import httpx

from . import llm
from .decide import check_say
from .live import Limiter

MODEL = "google/gemini-2.5-flash-lite"  # explicit: main's default (gpt-oss) is about 2x slower and dearer
PER_CALL, IN_FLIGHT = 20, 24
FIRST_ACTIONS = ("finish_task", "gather_belongings", "seek_info", "leave")
CONVINCED = ("alarm", "smoke", "staff", "crowd")
EXIT_PREFS = ("familiar", "nearest", "signed")
Z95 = 1.645
SYSTEM = """You write behaviour playbooks for simulated building occupants, BEFORE anything happens.
For each person, say how THIS person would respond if the fire alarm went off right now, given who they are
and what they are doing. Research on real evacuations: people rarely panic; they delay, finish tasks, gather
belongings, look for information, go to familiar exits and people; staff act quickly and direct others;
visitors follow staff and crowds; skeptics wait for smoke or several people telling them.

Fields, per person:
- first_action: what they do first after the alarm: "finish_task", "gather_belongings", "seek_info" or "leave".
- premove_s: seconds after noticing the alarm before they start moving to leave (a number).
- convinced_by: what finally makes them leave: "alarm", "smoke", "staff" or "crowd".
- crowd_n: how many people leaving around them would make them go too (1 to 10).
- exit_preference: "familiar" (the way they came in), "nearest" or "signed" (follow exit signs).
- official_trust, rumor_trust: 0 to 1, how much they believe staff or announcements, and hearsay.
- helps_others: true if they would stop to help or warn others.
- lines: 3 short things they might say out loud (at most 8 words each).

Return JSON only: {"playbooks": [{"pid": "<id>", "first_action": ..., "premove_s": ..., "convinced_by": ...,
"crowd_n": ..., "exit_preference": ..., "official_trust": ..., "rumor_trust": ..., "helps_others": ...,
"lines": ["...", "...", "..."]}, ...]} with one entry per person, in the order given."""

# persona defaults when the LLM gives nothing usable (research/EVAC_PSYCHOLOGY.md archetypes)
DEFAULTS = {"staff": ("leave", "staff", True), "regular": ("finish_task", "alarm", False),
            "visitor": ("seek_info", "crowd", False), "committed": ("finish_task", "staff", False),
            "parent": ("gather_belongings", "alarm", True), "older_adult": ("gather_belongings", "staff", False),
            "mobility_impaired": ("seek_info", "staff", False), "skeptic": ("finish_task", "smoke", False)}


def _sim_persona(name: str):
    from sim.personas import resolve  # sim owns the trait ranges; imported lazily so cognition stays light
    return resolve(name)


def premove_range(persona_name: str) -> tuple[float, float]:
    p = _sim_persona(persona_name)
    return (p.premove_median * math.exp(-Z95 * p.premove_sigma), p.premove_median * math.exp(Z95 * p.premove_sigma))


def _num(v: object, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def clamp(raw: dict, persona_name: str) -> dict:
    """Validated playbook: enums checked, numbers clamped to the persona's research ranges."""
    p = _sim_persona(persona_name)
    lo, hi = premove_range(persona_name)
    lo, hi = math.ceil(lo * 10) / 10, math.floor(hi * 10) / 10  # stay inside after rounding to 0.1 s
    d_first, d_conv, d_help = DEFAULTS.get(p.name, DEFAULTS["regular"])
    first = raw.get("first_action") if raw.get("first_action") in FIRST_ACTIONS else d_first
    conv = raw.get("convinced_by") if raw.get("convinced_by") in CONVINCED else d_conv
    if str(raw.get("convinced_by", "")).startswith("crowd"):
        conv = "crowd"
    premove = min(hi, max(lo, _num(raw.get("premove_s"), p.premove_median)))
    if first == "leave":
        premove = min(premove, p.premove_median)  # leaving first cannot mean a long delay
    lines = [s.strip() for s in raw.get("lines") or [] if isinstance(s, str) and s.strip() and not check_say(s)]
    out = {"first_action": first, "premove_s": round(premove, 1), "convinced_by": conv,
           "crowd_n": int(min(10, max(1, round(_num(raw.get("crowd_n"), p.social_threshold if p.social_threshold < 99
                                                    else 10))))),
           "exit_preference": raw.get("exit_preference") if raw.get("exit_preference") in EXIT_PREFS else "familiar",
           "official_trust": round(min(1.0, max(0.0, _num(raw.get("official_trust"), 0.7))), 2),
           "rumor_trust": round(min(1.0, max(0.0, _num(raw.get("rumor_trust"), 0.4))), 2),
           "helps_others": bool(raw.get("helps_others")) if isinstance(raw.get("helps_others"), bool) else d_help,
           "lines": lines[:3]}
    if p.name == "mobility_impaired":
        out["helps_others"] = False
    if p.name == "skeptic":
        out["crowd_n"] = max(out["crowd_n"], 2)
    return out


def to_traits(pb: dict) -> dict:
    """Playbook -> the sim's per-occupant traits (plus the playbook itself, passed through to cognition)."""
    t = {"premove_s": pb["premove_s"], "social_threshold": pb["crowd_n"] if pb["convinced_by"] != "smoke" else 10,
         "sign_follow_p": {"signed": 0.9, "nearest": 0.5, "familiar": 0.2}[pb["exit_preference"]],
         "risk_tolerance": round(1.0 - pb["official_trust"] * 0.5 - (0.2 if pb["first_action"] == "leave" else 0), 2),
         "playbook": pb}
    if pb["exit_preference"] == "nearest":
        t["exit_familiarity"] = 0.8
    return t


def expand(scenario: dict) -> list[dict]:
    """One person per occupant slot: persona drawn from the group's mix (seeded), room kept."""
    from sim.personas import resolve
    rng = random.Random(f"playbook|{scenario.get('seed', 0)}")
    people, total = [], sum(int(g.get("count", 0)) for g in scenario["occupants"])
    width, row = max(2, len(str(total))), 0
    for gi, g in enumerate(scenario["occupants"]):
        mix = g.get("personas") or {"regular": 1.0}
        for k in range(int(g.get("count", 0))):
            persona = rng.choices(list(mix), weights=list(mix.values()))[0]
            row += 1
            people.append({"pid": f"p{len(people) + 1:05d}", "agent_id": f"a_{row:0{width}d}", "group": gi,
                           "persona": persona, "where": g.get("where") or {}, "extra": {
                               k2: v for k2, v in g.items() if k2 not in ("count", "personas", "models", "traits",
                                                                         "where")}})
            if resolve(persona).name == "parent" and g.get("children", True) is not False:
                row += 1  # the sim spawns the child right after its parent
    return people


def _describe(person: dict) -> str:
    room = person["where"].get("room") or person["where"].get("level") or "somewhere"
    return f"- {person['pid']}: {person['persona']} in {room}"


async def _write_batch(client, limiter: Limiter, batch: list[dict], context: str) -> tuple[dict, dict]:
    user = f"SETTING: {context}\nPEOPLE:\n" + "\n".join(_describe(p) for p in batch)
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    for attempt in range(4):
        try:
            async with limiter:
                raw, meta = await llm.achat(client, msgs, model=MODEL, temperature=0.9, max_tokens=150 * len(batch),
                                            timeout=60.0)
            limiter.ok()
            got = {str(x.get("pid")): x for x in llm.parse_json(raw).get("playbooks") or [] if isinstance(x, dict)}
            return got, meta
        except llm.RateLimited as e:
            limiter.backoff()
            await asyncio.sleep(min(4.0, e.retry_after) * (1 + attempt))
        except Exception as e:  # bad JSON, timeout, HTTP error: these people get persona defaults
            return {}, {"cost_usd": 0.0, "latency_ms": 0, "cached": False, "error": f"{type(e).__name__}: {e}"}
    return {}, {"cost_usd": 0.0, "latency_ms": 0, "cached": False, "error": "429 retries exhausted"}


async def _write_all(people: list[dict], context: str) -> list[tuple[dict, dict, int]]:
    batches = [people[i:i + PER_CALL] for i in range(0, len(people), PER_CALL)]
    limiter = Limiter(IN_FLIGHT)
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=IN_FLIGHT)) as client:
        res = await asyncio.gather(*(_write_batch(client, limiter, b, context) for b in batches))
    return [(got, meta, i) for i, (got, meta) in enumerate(res)]


def _context(scenario: dict) -> str:
    return "; ".join(scenario.get("assumptions") or [])[:600] or "an office building on a weekday"


def build(scenario_path: Path, out: Path, offline: bool = False) -> dict:
    scenario = json.loads(scenario_path.read_text())
    digest = hashlib.sha256(scenario_path.read_bytes()).hexdigest()[:16]
    people = expand(scenario)
    pb_file = out / "playbooks.jsonl"
    rows: list[dict] = []
    if pb_file.exists():
        old = [json.loads(x) for x in pb_file.read_text().splitlines() if x.strip()]
        if old and all(r.get("scenario_sha") == digest for r in old) and len(old) == len(people):
            rows = old  # replay: same scenario, same playbooks, no calls
    stats = {"calls": 0, "cost_usd": 0.0, "latency_ms": [], "reused": bool(rows), "defaults": 0}
    if not rows:
        written = {} if offline else asyncio.run(_write_all(people, _context(scenario)))
        by_pid, meta_of = {}, {}
        for got, meta, bi in (written or []):
            by_pid.update(got)
            stats["calls"] += 0 if meta.get("cached") else 1
            stats["cost_usd"] += meta.get("cost_usd", 0.0)
            stats["latency_ms"].append(meta.get("latency_ms", 0))
            for p in people[bi * PER_CALL:(bi + 1) * PER_CALL]:
                meta_of[p["pid"]] = {"batch": bi, "error": meta.get("error")}
        for p in people:
            raw = by_pid.get(p["pid"]) or {}
            stats["defaults"] += 0 if raw else 1
            rows.append({"pid": p["pid"], "agent_id": p["agent_id"], "persona": p["persona"], "where": p["where"],
                         "group": p["group"], "extra": p["extra"], "playbook": clamp(raw, p["persona"]),
                         "raw": raw, "source": "llm" if raw else "default", **meta_of.get(p["pid"], {}),
                         "scenario_sha": digest, "model": MODEL})
        out.mkdir(parents=True, exist_ok=True)
        pb_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    hybrid = {**scenario, "occupants": [{"where": r["where"], "count": 1, "personas": {r["persona"]: 1.0},
                                         "models": {MODEL: 1.0}, "traits": to_traits(r["playbook"]), **r["extra"]}
                                        for r in rows]}
    hybrid["assumptions"] = list(scenario.get("assumptions") or []) + [
        f"Hybrid occupants: one playbook per person written before the run by {MODEL} ({pb_file.name})."]
    (out / "scenario_hybrid.json").write_text(json.dumps(hybrid, indent=1))
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--offline", action="store_true", help="persona defaults only, no calls")
    a = ap.parse_args()
    s = build(Path(a.scenario), Path(a.out), a.offline)
    lat = sorted(s["latency_ms"])
    print(json.dumps({**{k: v for k, v in s.items() if k != "latency_ms"}, "cost_usd": round(s["cost_usd"], 4),
                      "latency_ms_p50": lat[len(lat) // 2] if lat else None,
                      "latency_ms_max": lat[-1] if lat else None}))


if __name__ == "__main__":
    main()
