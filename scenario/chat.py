"""Plain language -> scenario.json with LLM tool calling (OpenAI-compatible, OpenRouter).

The model never invents ids: it must call resolve_location (code, scenario/resolve.py),
ask the user when a place is unknown or ambiguous, and submit the whole scenario
to submit_scenario, which validates it (scenario/schema.py) and returns the
errors for repair. Calls are cached on disk by request hash (data/llm_cache/),
so reruns and tests are free. Key: OPENROUTER_API_KEY or the repo .env.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import urllib.request
from typing import Callable

import certifi

from analysis.occupancy import analyze as occupancy
from ingest_lidar.semantics import CACHE, _key

from .resolve import _pool, resolve_location
from .schema import DEFAULT_MODEL, DEFAULT_PERSONAS, EVENT_TYPES, PERSONAS, normalize, validate

CHAT_MODEL = os.environ.get("SCENARIO_MODEL", "anthropic/claude-haiku-5.5")  # flash-lite skipped ask_user and stalled in tests
MAX_TURNS = 12
ONE_SHOT_ANSWER = ("No user is available (one-shot mode). Pick the most likely candidate, write that "
                   "choice into assumptions, and call submit_scenario.")


def one_shot_answer(question: str, options: list[str]) -> str:
    """ask_user stand-in for one-shot mode: take the first (best ranked) option, deterministically."""
    if not options:
        return ONE_SHOT_ANSWER
    return (f"No user is available (one-shot mode). Use {options[0]!r}, write 'assumed {options[0]} for: "
            f"{question}' into assumptions, and continue.")

_AT = {"type": "object", "description": "Exactly one of room, exit, door, or point+level",
       "properties": {"room": {"type": "string"}, "exit": {"type": "string"}, "door": {"type": "string"},
                      "level": {"type": "string"},
                      "point": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}}}
TOOLS = [
    {"type": "function", "function": {
        "name": "resolve_location",
        "description": "Look up rooms, exits, doors or levels in the loaded building by the user's words. "
                       "Always call this before using an id.",
        "parameters": {"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string", "description": "User words, e.g. 'kitchen', 'east exit', 'floor 2'"},
            "kind": {"type": "string", "enum": ["room", "exit", "door", "level", "any"]}}}}},
    {"type": "function", "function": {
        "name": "ask_user",
        "description": "Ask one clarifying question when a place is unknown or ambiguous or a required value "
                       "is missing. Offer the candidates as options.",
        "parameters": {"type": "object", "required": ["question"], "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}, "maxItems": 6}}}}},
    {"type": "function", "function": {
        "name": "submit_scenario",
        "description": "Submit the complete scenario. Returns ok or a list of validation errors to fix.",
        "parameters": {"type": "object", "required": ["events", "occupants", "duration_s", "assumptions"],
                       "properties": {
            "duration_s": {"type": "integer", "minimum": 60, "maximum": 3600},
            "events": {"type": "array", "items": {"type": "object", "required": ["id", "type", "t_start_s", "at"],
                                                  "properties": {
                "id": {"type": "string"}, "type": {"type": "string", "enum": list(EVENT_TYPES)},
                "t_start_s": {"type": "number", "minimum": 0}, "at": _AT,
                "params": {"type": "object", "description": "Type-specific; omit for defaults. rumor: "
                           "{text, claim: {exit_blocked: exit id}, radius_m}; fire/smoke: {block_after_s}"}}}},
            "occupants": {"type": "array", "items": {"type": "object", "required": ["where", "count"], "properties": {
                "where": {"type": "object", "description": "{room: id} or {all: true} (spread by room capacity)",
                          "properties": {"room": {"type": "string"}, "all": {"type": "boolean"}}},
                "count": {"type": "integer", "minimum": 1, "maximum": 2000},
                "personas": {"type": "object", "additionalProperties": {"type": "number"}}}}},
            "assumptions": {"type": "array", "items": {"type": "string"}}}}}},
]


def _system(scene: dict) -> str:
    rooms = "; ".join(f'{r["id"]} "{r["name"]}" ({r["type"]}, {r["area_m2"]:.0f} m2, {r["level"]})'
                      for r in scene["rooms"] if r["type"] != "outside")
    exits = "; ".join(f'{e["id"]} "{e["label"]}" ({e["width_m"]:.2f} m wide, at [{e["position"][0]:.1f}, '
                      f'{e["position"][1]:.1f}])' for e in scene["exits"])
    levels = "; ".join(f'{lv["id"]} "{lv["name"]}"' for lv in scene["levels"])
    mix = ", ".join(f"{v} {k}" for k, v in DEFAULT_PERSONAS.items())
    return f"""You turn a user's description of an emergency drill into a scenario for an evacuation simulator.
The building is fixed (below). Rules:
- Use only ids returned by resolve_location. Never invent ids.
- If resolve_location returns ambiguous=true (no match, or several equally good), call ask_user with
  the candidates as options before submitting. Do not guess. If ask_user says no user is available,
  pick the best candidate and write the choice and the reason into assumptions.
- Times are seconds from scenario start ("at 1 minute" = 60). Default duration_s 300.
- Event types: {", ".join(EVENT_TYPES)}. fire/smoke: the room and its doors become impassable after
  params.block_after_s (default 60). blocked_exit: at {{"exit": id}}. rumor: params.text (the message),
  params.claim (e.g. {{"exit_blocked": "<exit id>"}}), at = where it starts (a room).
- Occupants: where {{"room": id}} or {{"all": true}} to spread a total over the building.
- Personas: {", ".join(PERSONAS[:8])}. If the user gives no mix, omit personas (default: {mix}).
- Unstated values: use defaults and write each guess into assumptions.
- Call submit_scenario once everything is resolved; if it returns errors, fix them or ask the user.
BUILDING "{scene["meta"]["name"]}": levels: {levels}. Rooms: {rooms}. Exits: {exits}."""


def call_llm(messages: list[dict], tools: list[dict], tool_choice: str = "auto",
             model: str = CHAT_MODEL) -> tuple[dict, float]:
    """One cached chat completion with tools; returns (assistant message, cost in USD, 0 when cached)."""
    body = {"model": model, "messages": messages, "tools": tools, "tool_choice": tool_choice,
            "temperature": 0, "max_tokens": 1500,
            "reasoning": {"effort": "none"}, "usage": {"include": True}}
    raw = json.dumps(body, sort_keys=True).encode()
    hit = CACHE / f"chat_{hashlib.sha256(raw).hexdigest()}.json"
    if hit.exists():
        return json.loads(hit.read_text())["message"], 0.0
    key = _key()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set (env or repo .env)")
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", raw,
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90, context=ssl.create_default_context(cafile=certifi.where())) as r:
        resp = json.load(r)
    if "choices" not in resp:
        raise RuntimeError(f"OpenRouter error: {str(resp.get('error', resp))[:300]}")
    msg = resp["choices"][0]["message"]
    msg = {k: msg[k] for k in ("role", "content", "tool_calls") if msg.get(k) is not None}
    CACHE.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps({"model": model, "message": msg}))
    return msg, float(resp.get("usage", {}).get("cost") or 0.0)


def _resolve(scene: dict, args: dict) -> dict:
    """resolve_location plus, when nothing matched, the full list to offer the user."""
    kind = args.get("kind", "any")
    result = resolve_location(scene, args.get("text", ""), kind)
    if not result["candidates"]:
        want = kind if kind in ("exit", "door", "level") else "room"
        result["all_options"] = [f'{c["label"]} ({c["id"]})' for c in _pool(scene) if c["kind"] == want][:12]
        result["next"] = "no match: call ask_user with all_options"
    elif result["ambiguous"]:
        result["next"] = "ambiguous: call ask_user with these candidates"
    return result


def expand_occupants(groups: list[dict], scene: dict) -> list[dict]:
    """Turn {"where": {"all": true}, "count": n} into per-room groups, split by occupant load (largest remainder)."""
    loads = {k: v["load"] for k, v in occupancy(scene)["rooms"].items()}
    if not any(loads.values()):  # ponytail: nothing has a load (all circulation), fall back to area
        loads = {r["id"]: r["area_m2"] for r in scene["rooms"] if r["type"] != "outside"}
    out = []
    for g in groups:
        where = g.get("where") if isinstance(g, dict) else None
        if not isinstance(where, dict) or "room" in where or not isinstance(g.get("count"), int):
            out.append(g)  # left as is: room groups, or malformed ones the validator will report
            continue
        total, n = sum(loads.values()), g.get("count", 0)
        share = {rid: n * w / total for rid, w in loads.items() if w > 0}
        counts = {rid: int(v) for rid, v in share.items()}
        for rid in sorted(share, key=lambda r: share[r] - counts[r], reverse=True)[:n - sum(counts.values())]:
            counts[rid] += 1
        rest = {k: v for k, v in g.items() if k not in ("where", "count")}
        out += [{"where": {"room": rid}, "count": c, **rest} for rid, c in counts.items() if c > 0]
    return out


def build(args: dict, scene: dict, scene_path: str, seed: int = 42) -> dict:
    return {"schema_version": "0.1", "scene": scene_path, "seed": seed, "duration_s": args.get("duration_s", 300),
            "events": [{**e, "params": e.get("params") or {}} for e in args.get("events", [])],
            "occupants": expand_occupants(args.get("occupants", []), scene),
            "assumptions": args.get("assumptions", [])}


def run(scene: dict, scene_path: str, request: str, ask: Callable[[str, list[str]], str],
        llm: Callable = call_llm, log: Callable[[str], None] = lambda s: None,
        tool_choice: str = "auto") -> tuple[dict | None, float, list]:
    """Drive the tool loop until a valid scenario is submitted. Returns (scenario or None, cost, messages).

    tool_choice="required" (one-shot mode) stops weak models from answering in prose.
    """
    messages = [{"role": "system", "content": _system(scene)}, {"role": "user", "content": request}]
    cost = 0.0
    for _ in range(MAX_TURNS):
        msg, c = llm(messages, TOOLS, tool_choice)
        cost += c
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:  # the model asked in prose instead of using ask_user
            messages.append({"role": "user", "content": ask(msg.get("content") or "", [])})
            continue
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError as e:
                args = {"_bad_json": str(e)}
            if "_bad_json" in args:
                result = {"error": f"arguments are not valid JSON: {args['_bad_json']}"}
            elif name == "resolve_location":
                result = _resolve(scene, args)
            elif name == "ask_user":
                result = {"answer": ask(args.get("question", ""), args.get("options", []))}
            elif name == "submit_scenario":
                try:
                    scenario = build(args, scene, scene_path)
                    errors = validate(scenario, scene)
                except (AttributeError, TypeError, ValueError) as e:  # malformed shapes from the model
                    errors = [f"malformed scenario: {type(e).__name__}: {e}"]
                if not errors:
                    log("submit_scenario -> ok")
                    return normalize(scenario), cost, messages
                result = {"ok": False, "errors": errors}
            else:
                result = {"error": f"unknown tool {name}"}
            log(f"{name}({json.dumps(args)[:200]}) -> {json.dumps(result)[:300]}")
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
    return None, cost, messages
