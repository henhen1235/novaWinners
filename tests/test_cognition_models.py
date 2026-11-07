"""Per-model request shapes in cognition/llm.py, checked against mocked HTTP (no network)."""

import asyncio
import io
import json

import httpx
import pytest

from cognition import llm
from cognition.schemas import Decision

MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "state"}]
REPLY = {"choices": [{"message": {"content": '{"ok": 1}'}}], "usage": {"cost": 0.0}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "CACHE", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


def sent_body(monkeypatch, model):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout, context):
        seen["body"] = json.loads(req.data)
        return Resp(json.dumps(REPLY).encode())
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    llm.chat(MSGS, model=model, max_tokens=260)
    return seen["body"]


def test_default_body_unchanged(monkeypatch):
    b = sent_body(monkeypatch, "google/gemini-2.5-flash-lite")
    assert b["reasoning"] == {"enabled": False} and b["response_format"] == {"type": "json_object"}
    assert b["max_tokens"] == 260 and b["messages"] == MSGS


def test_gpt_oss_gets_low_reasoning_and_room(monkeypatch):
    b = sent_body(monkeypatch, "openai/gpt-oss-120b")
    assert b["reasoning"] == {"effort": "low"} and b["max_tokens"] == 400
    assert b["response_format"] == {"type": "json_object"}


def test_llama_gets_no_response_format_or_reasoning_and_a_json_hint(monkeypatch):
    b = sent_body(monkeypatch, "meta-llama/llama-4-scout")
    assert "response_format" not in b and "reasoning" not in b
    assert b["messages"][-1]["content"].endswith(llm.JSON_ONLY) and MSGS[-1]["content"] == "state"


def test_async_path_uses_the_same_shapes():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=REPLY)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            for m in ("openai/gpt-oss-120b", "meta-llama/llama-4-scout", "google/gemini-2.5-flash-lite"):
                await llm.achat(c, MSGS, model=m)
    asyncio.run(go())
    assert seen[0]["reasoning"] == {"effort": "low"} and "response_format" not in seen[1]
    assert seen[2]["reasoning"] == {"enabled": False}


def test_rate_limit_raises_rate_limited():
    async def go():
        t = httpx.MockTransport(lambda r: httpx.Response(429, headers={"retry-after": "2"}))
        async with httpx.AsyncClient(transport=t) as c:
            await llm.achat(c, MSGS)
    with pytest.raises(llm.RateLimited):
        asyncio.run(go())


@pytest.mark.parametrize("raw,want", [("0.8", 0.8), ("80%", 0.8), (80, 0.8), ("high", 0.8), ("??", 0.5),
                                      (1.7, 1.0), (-0.2, 0.0), (0.4, 0.4)])
def test_confidence_is_coerced(raw, want):
    d = Decision.model_validate({"padm": {"threat_real": "yes", "need_action": "yes", "options_considered": ["o1"],
                                          "choice": "o1", "why": "x", "choice_evidence": ["p1"], "when": "now"},
                                 "confidence": raw})
    assert d.confidence == pytest.approx(want)


def test_nested_fields_are_hoisted():
    d = Decision.model_validate({"padm": {"threat_real": "yes", "need_action": "yes", "options_considered": ["o1"],
                                          "choice": "o1", "why": "x", "choice_evidence": ["p1"], "when": "now",
                                          "confidence": "0.7", "say": "Go"},
                                 "feel": {"fear": 0.6, "hope": 0.2}})
    assert d.confidence == 0.7 and d.say == "Go" and d.feel == "fear"
