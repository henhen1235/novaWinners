"""cognition.live with a mocked async LLM: batching, dedupe, prefetch, deadlines, UI traits."""

import asyncio
import importlib
import json
import re

import pytest

from cognition import llm
from cognition.live import BATCH_SYSTEM, Live, batch_sizes
from tests.test_cognition import fake_chat, view

D = importlib.import_module("cognition.decide")
CALLS: list[int] = []


async def fake_achat(client, messages, **kw):
    if messages[0]["content"] != BATCH_SYSTEM:
        CALLS.append(1)
        return fake_chat(messages)
    parts = re.split(r"^### OCCUPANT (\S+)\n", messages[1]["content"], flags=re.M)[1:]
    CALLS.append(len(parts) // 2)
    out = []
    for aid, body in zip(parts[::2], parts[1::2]):
        one = json.loads(fake_chat([messages[0], {"role": "user", "content": body}])[0])
        if aid == "a_bad":
            one["padm"]["choice_evidence"] = ["p99"]
        out.append({"agent_id": aid, **one})
    return json.dumps({"decisions": out}), {"cached": False, "latency_ms": 5, "cost_usd": 0.0005}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    CALLS.clear()
    monkeypatch.setattr(llm, "achat", fake_achat)
    D.configure(offline=False, run_id="t")
    yield


def distinct_views(n, bad=None):
    vs = []
    for i in range(n):
        v = view(aid=bad if i == 0 and bad else f"a_{i:02d}", smoke=i % 2 == 0, smoke_m=2.0 + 2 * i)
        v["agent"]["traits"]["risk_tolerance"] = round(i / n, 3)  # every state differs
        vs.append(v)
    return vs


def test_batch_sizes():
    assert batch_sizes(5) == [1] * 5 and batch_sizes(8) == [1] * 8
    assert batch_sizes(12) == [6, 6] and batch_sizes(20) == [10, 10] and batch_sizes(25) == [9, 8, 8]


def test_burst_is_batched_and_split_back_per_agent():
    live = Live()
    vs = distinct_views(20)
    hs = live.submit(vs)
    got = {r["handle"]: r for r in live.wait(20)}
    live.close()
    assert sorted(CALLS) == [10, 10]
    assert set(got) == set(hs)
    for h, v in zip(hs, vs):
        rec = got[h]["record"]
        assert rec["agent_id"] == v["agent"]["id"] and rec["batch"]["size"] == 10
        assert rec["validation"]["status"] == "ok"
        assert got[h]["choice"] == ("o2" if any(p["kind"] == "smoke" for p in v["percepts"]) else rec["decision"]["padm"]["choice"])
    assert len({r["record"]["record_id"] for r in got.values()}) == 20


def test_bad_entry_in_batch_is_repaired_alone():
    live = Live()
    got = live.wait(len(live.submit(distinct_views(12, bad="a_bad"))))
    live.close()
    bad = next(r for r in got if r["agent_id"] == "a_bad")["record"]
    assert bad["validation"]["status"] == "repaired" and "unknown evidence ids" in bad["validation"]["errors"][0]
    assert all(r["record"]["validation"]["status"] == "ok" for r in got if r["agent_id"] != "a_bad")
    assert sorted(CALLS) == [1, 6, 6]


def test_unbatched_mode_sends_one_call_per_state():
    live = Live(batching=False)
    live.wait(len(live.submit(distinct_views(12))))
    live.close()
    assert CALLS == [1] * 12


def test_identical_states_share_one_call():
    live = Live()
    vs = [view(aid=f"a_{i}") for i in range(3)]
    got = live.wait(len(live.submit(vs)))
    live.close()
    assert len(CALLS) == 1 and len(got) == 3
    assert sum("shared_with" in r["record"] for r in got) == 2
    assert {r["record"]["agent_id"] for r in got} == {"a_0", "a_1", "a_2"}


def test_prefetch_is_reused_by_a_later_identical_state():
    live = Live()
    live.submit([{**view(aid="a_1", tick=10), "prefetch": True}])
    assert live.wait(1, timeout=0.3) == []  # prefetches are not delivered on their own
    got = live.wait(len(live.submit([view(aid="a_1", tick=14)])))
    live.close()
    assert len(CALLS) == 1 and got[0]["record"].get("prefetched") is True and got[0]["tick"] == 14


def test_expired_handle_gets_fallback_and_late_answer_is_dropped(monkeypatch):
    async def slow(client, messages, **kw):
        await asyncio.sleep(0.3)
        return await fake_achat(client, messages, **kw)
    monkeypatch.setattr(llm, "achat", slow)
    live = Live()
    v = view(aid="a_late")
    h = live.submit([v])[0]
    r = live.expire(h, v)
    assert r["record"]["validation"]["status"] == "fallback_deadline"
    assert live.wait(1, timeout=0.8) == []
    live.close()


def test_offline_answers_immediately_without_calls():
    D.configure(offline=True)
    live = Live()
    got = live.wait(len(live.submit(distinct_views(10))), timeout=1)
    assert len(got) == 10 and not CALLS and all(r["record"]["source"] == "fallback" for r in got)


def test_async_twin_returns_all():
    live = Live()
    got = asyncio.run(live.adecide(distinct_views(4)))
    live.close()
    assert len(got) == 4


def test_ui_traits_change_prompt_and_appraisal():
    from cognition.personas import contagion_traits
    base = view(aid="a_t1")
    loud = view(aid="a_t2")
    loud["agent"]["traits"].update({"ocean": {"E": 0.9, "N": 0.8}, "speed_mps": 0.7, "exit_familiarity": 0.1,
                                    "sign_follow_p": 1.0, "social_threshold": 1, "risk_tolerance": 0.9})
    for v in (base, loud):
        v["percepts"] += [{"id": "p8", "kind": "people_moving", "count": 1},
                          {"id": "p9", "kind": "exit_sign", "ref": "e_002"}]
    sb, sl = (D.compute_state(D.build_inputs(v)) for v in (base, loud))
    user = D.prompt_for(D.build_inputs(loud), sl)[1]["content"]
    assert "outgoing and talkative" in user and "easily alarmed" in user and "walk slowly" in user
    assert "Personality" not in D.prompt_for(D.build_inputs(base), sb)[1]["content"].split("YOU:")[1]
    assert sl["appraisal"]["social_proof"] > sb["appraisal"]["social_proof"]  # threshold 1 vs default 4
    assert sl["appraisal"]["coping"] < sb["appraisal"]["coping"]  # slow, unfamiliar
    assert "p9" in {p["id"] for p in sl["percepts"]}  # always follows signs
    t = loud["agent"]["traits"]
    assert contagion_traits("visitor", t)["eps"] > contagion_traits("visitor")["eps"]
