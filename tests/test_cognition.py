"""cognition/ without network: the LLM is replaced by a fake that reads the prompt."""

import json
import re

import pytest

import importlib

D = importlib.import_module("cognition.decide")
from cognition import llm
from cognition.appraisal import appraise, emotions, update_beliefs
from cognition.interview import COMPOSE_SYSTEM, interview


def view(aid="a_01", persona="visitor", tick=10, smoke=True, smoke_m=6.0, rumor=False, people=()):
    ps = [{"id": "p1", "kind": "alarm", "channel": "hearing"}]
    if smoke:
        ps.append({"id": "p2", "kind": "smoke", "where": "r_003", "dist_m": smoke_m})
    if rumor:
        ps.append({"id": "m_1", "kind": "utterance", "channel": "hearing", "from": "a_09",
                   "text": "Exit 1 is locked", "claim": {"exit_blocked": "e_001"}})
    for i, (who, doing, d) in enumerate(people):
        ps.append({"id": f"p{10 + i}", "kind": "person", "ref": who, "doing": doing, "dist_m": d})
    return {"agent": {"id": aid, "persona": persona, "room": "r_002", "known_exits": ["e_001", "e_002"],
                      "traits": {"entry_exit": "e_001"}},
            "t_s": tick * 0.5, "tick": tick, "trigger": {"kind": "cue_onset", "ref": "p1"}, "percepts": ps,
            "beliefs": [],
            "options": [{"id": "o1", "action": "EVACUATE", "exit": "e_001", "route_m": 14.0, "queue_s": 20},
                        {"id": "o2", "action": "EVACUATE", "exit": "e_002", "route_m": 22.0, "queue_s": 0},
                        {"id": "o3", "action": "SEEK_INFO"}, {"id": "o4", "action": "CONTINUE_TASK"}]}


def fake_chat(messages, **kw):
    if messages[0]["content"] == COMPOSE_SYSTEM:
        tags = re.findall(r"\[t=[^\]]+\]", messages[1]["content"])
        return json.dumps({"sentences": [f"I left because of the smoke. {tags[0]}",
                                         "I also saw a dragon. [t=1, d_99999_a_99]", "No citation here."]}), \
            {"cached": False, "latency_ms": 1, "cost_usd": 0.0}
    user = messages[1]["content"]
    smoke = re.search(r"^- (p\d+) \[sight\] smoke", user, re.M)
    choice = "o2" if smoke else "o4"
    ev = [smoke.group(1)] if smoke else ["p1"]
    out = {"padm": {"threat_real": "yes" if smoke else "unsure", "threat_evidence": ev,
                    "need_action": "yes" if smoke else "later", "need_evidence": ev,
                    "options_considered": ["o2", "o4"], "choice": choice, "why": "test",
                    "choice_evidence": ev, "when": "now" if smoke else "after_task"},
           "say": "Go now" if smoke else None, "feel": "afraid", "confidence": 0.6}
    return json.dumps(out), {"cached": False, "latency_ms": 5, "cost_usd": 0.0001}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(llm, "chat", fake_chat)
    D.configure(offline=False, run_id="t")
    yield
    D.configure(offline=False)


IDS = {"p1", "p2", "b1", "o1", "o2", "o3", "o4"}
OPTS = view()["options"]


def raw(**over):
    padm = {"threat_real": "yes", "threat_evidence": ["p2"], "need_action": "yes", "need_evidence": ["p2"],
            "options_considered": ["o2"], "choice": "o2", "why": "smoke", "choice_evidence": ["p2"], "when": "now"}
    padm.update(over)
    return json.dumps({"padm": padm, "say": "Go", "feel": "afraid", "confidence": 0.5})


def test_validator_accepts_good_and_rejects_bad_ids():
    assert D.validate(raw(), IDS, OPTS)[0] is not None
    for bad in (raw(choice="o9", options_considered=["o9"]), raw(choice_evidence=["p99"]),
                raw(threat_evidence=["b7"]), raw(choice_evidence=[]), raw(need_action="no"), "not json"):
        dec, errs = D.validate(bad, IDS, OPTS)
        assert dec is None and errs


def test_validator_content_filter():
    long_say = json.loads(raw())
    long_say["say"] = "go " * 20
    assert D.validate(json.dumps(long_say), IDS, OPTS)[0] is None
    bad = json.loads(raw())
    bad["say"] = "see http://example.com"
    assert D.validate(json.dumps(bad), IDS, OPTS)[0] is None


def test_offline_fallback_picks_offered_option():
    D.configure(offline=True)
    for persona in ("visitor", "skeptic", "warden", "parent_with_child"):
        r = D.decide(view(aid=f"a_{persona}", persona=persona))
        assert r["record"]["source"] == "fallback"
        assert r["choice"] in {o["id"] for o in OPTS}


def test_skeptic_ignores_alarm_alone_offline():
    D.configure(offline=True)
    r = D.decide(view(persona="skeptic", smoke=False))
    assert r["choice"] == "o4"


def test_invalid_llm_output_repairs_once_then_falls_back(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", lambda m, **k: (calls.append(1), ("nope", {"cached": False, "latency_ms": 1,
                                                                              "cost_usd": 0.0}))[1])
    r = D.decide(view())
    assert len(calls) == 2 and r["record"]["validation"]["status"] == "fallback"


def test_llm_decision_is_recorded_with_valid_evidence():
    rec = D.decide(view())["record"]
    assert rec["source"] == "llm" and rec["validation"]["status"] == "ok"
    ids = {p["id"] for p in rec["percepts"]} | {b["id"] for b in rec["beliefs_after"]}
    assert set(rec["decision"]["padm"]["choice_evidence"]) <= ids


def test_emotions_bounded_and_threat_raises_fear():
    st = lambda v: D.compute_state(D.build_inputs(v))  # noqa: E731
    calm, far, near = st(view(smoke=False)), st(view(smoke_m=20)), st(view(smoke_m=2))
    for s in (calm, far, near):
        assert all(0 <= s["emotion"][k] <= 1 for k in ("fear", "anxiety", "hope", "relief", "frustration"))
        assert -1 <= s["emotion"]["valence"] <= 1
    assert calm["emotion"]["fear"] < far["emotion"]["fear"] < near["emotion"]["fear"]
    assert calm["appraisal"]["certainty"] < near["appraisal"]["certainty"]


def test_contagion_pulls_toward_neighbours():
    a = appraise([], [], [], "visitor", {})
    base, _ = emotions(a, None, 0.0, "visitor", [])
    afraid_nb = [{"agent": "a_2", "dist_m": 1.0, "fear": 0.9, "anxiety": 0.9, "eps": 0.6}]
    up, src = emotions(a, None, 0.0, "visitor", afraid_nb)
    assert up["fear"] > base["fear"] and any(s["from"] == "contagion" for s in src)
    scared = {"emotion": {**up, "fear": 0.8}, "t_s": 0.0, "harm": 0.0}
    calm_nb = [{"agent": "a_3", "dist_m": 1.0, "fear": 0.0, "anxiety": 0.0, "eps": 0.6}]
    down, _ = emotions(a, scared, 0.0, "visitor", calm_nb)
    alone, _ = emotions(a, scared, 0.0, "visitor", [])
    assert down["fear"] < alone["fear"]
    far, _ = emotions(a, None, 0.0, "visitor", [{**afraid_nb[0], "dist_m": 30.0}])
    assert far["fear"] < up["fear"]


def test_direct_observation_overrides_hearsay():
    rumor = {"id": "m_1", "kind": "utterance", "from": "a_9", "source_kind": "stranger",
             "claim": {"exit_blocked": "e_001"}}
    seen_open = {"id": "p5", "kind": "exit", "ref": "e_001", "channel": "sight", "blocked": False}
    b1 = update_beliefs([], [rumor], "visitor", 1)
    assert b1[0]["value"] is False
    b2 = update_beliefs(b1, [seen_open], "visitor", 2)
    assert b2[0]["value"] is True and b2[0]["conf"] == 1.0
    b3 = update_beliefs(b2, [{**rumor, "id": "m_2", "from": "a_8"}], "visitor", 3)
    assert b3[0]["value"] is True  # hearsay never overrides what I saw


def test_credibility_is_persona_dependent():
    rumor = {"id": "m_1", "kind": "utterance", "from": "a_9", "source_kind": "warden", "claim": {"hazard": "r_1"}}
    assert update_beliefs([], [rumor], "visitor", 1)[0]["conf"] > update_beliefs([], [rumor], "skeptic", 1)[0]["conf"]


def test_calm_agent_notices_static_sign_about_38_percent():
    sign = [{"id": "s", "kind": "exit_sign"}]
    seen = sum(bool(D.perceive(sign, 0.0, False, f"seed{i}")[0]) for i in range(2000))
    assert 0.33 < seen / 2000 < 0.43


def test_record_replay_returns_identical_decisions(tmp_path, monkeypatch):
    views = [view(aid=f"a_{i}", smoke=i % 2 == 0, tick=10 + i) for i in range(4)]
    first = [D.decide(v)["record"] for v in views]
    log = tmp_path / "decisions.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in first))

    def boom(*a, **k):
        raise AssertionError("replay must not call the LLM")
    monkeypatch.setattr(llm, "chat", boom)
    D.configure(replay=log, run_id="t")
    again = [D.decide(v) for v in views]
    assert [a["record"] for a in again] == first
    assert [a["choice"] for a in again] == [r["decision"]["padm"]["choice"] for r in first]


def test_rerun_of_record_rebuilds_the_same_prompt():
    rec = D.decide(view())["record"]
    inp = D.inputs_from_record(rec)
    import hashlib
    msgs = D.prompt_for(inp, D.compute_state(inp))
    assert hashlib.sha256(json.dumps(msgs).encode()).hexdigest() == rec["prompt_sha256"]


def test_interview_cites_only_existing_records(tmp_path):
    recs = [D.decide(view(aid="a_01", tick=t, smoke=t > 10))["record"] for t in (10, 20)]
    run = tmp_path / "run1"
    run.mkdir()
    (run / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    (run / "replay.json").write_text(json.dumps({"meta": {}, "events": [
        {"t": 10.0, "kind": "said", "agent": "a_01", "text": "Go now"}]}))
    ans = interview(run, "a_01", "Why did you go to exit 2?")
    ids = {r["record_id"] for r in recs}
    assert ans["answer"] and ans["citations"]
    assert all(c["record_id"] in ids for c in ans["citations"])
    assert "dragon" not in ans["answer"] and "No citation" not in ans["answer"]
    assert len(ans["dropped_sentences"]) == 2 and ans["verified"] is False
    smoke = next(r for r in ans["reasons"] if r["id"] == "p2")
    assert smoke["label"] == "necessary" and smoke["keep_rate_removed"] == 0.0
    assert ans["llm_calls"] <= 40
    cf = interview(run, "a_01", "What if you had not seen the smoke?")
    assert cf["counterfactual"]["counterfactual"] == {"o4": cf["counterfactual"]["n"]}
