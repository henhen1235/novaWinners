"""Shared tool-calling loop for the adversary and the optimizer (OpenRouter via scenario.chat.call_llm,
which caches every request on disk, so a rerun with the same inputs replays for free)."""

from __future__ import annotations

import json
import os
from typing import Callable

from scenario.chat import call_llm

MODEL = os.environ.get("LOOP_MODEL", "google/gemini-2.5-flash")
MAX_TURNS = 5

Handler = Callable[[str, dict], tuple[dict, bool]]  # (tool name, args) -> (result, finished)


def tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": props,
                                                            "required": required}}}


def tool_loop(system: str, user: str, tools: list[dict], handle: Handler, llm: Callable = call_llm,
              model: str = MODEL, max_turns: int = MAX_TURNS) -> dict:
    """Run until a handler says finished or max_turns. Returns {messages, cost_usd, llm_calls}."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    cost, calls_n = 0.0, 0
    for _ in range(max_turns):
        msg, c = llm(messages, tools, "required", model)
        cost, calls_n = cost + c, calls_n + 1
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            messages.append({"role": "user", "content": "Use the tools; call submit when done."})
            continue
        finished = False
        for call in calls:
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
                result, done = handle(call["function"]["name"], args)
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
                result, done = {"ok": False, "errors": [f"{type(e).__name__}: {e}"]}, False
            finished = finished or done
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
        if finished:
            break
    return {"messages": messages, "cost_usd": round(cost, 6), "llm_calls": calls_n}
