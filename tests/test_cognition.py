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
                    "options_considered": [choice], "choice": choice, "why": "test",
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


def stair_view(persona="regular_employee", smoky_east=False, aid="a_50", fire=True):
    """Hand-built 2-level view: agent on L2, exits on L1, east and west stairs plus a lift."""
    ps = [{"id": "p1", "kind": "alarm", "channel": "sound"},
          {"id": "p2", "kind": "stair", "ref": "st_east", "channel": "sight", "dist_m": 6.0,
           "smoke": smoky_east, "crowding": "low"},
          {"id": "p3", "kind": "stair", "ref": "st_west", "channel": "sight", "dist_m": 20.0, "crowding": "low"}]
    if fire:
        ps.append({"id": "p4", "kind": "smoke", "where": "L2_corridor", "dist_m": 8.0, "channel": "sight"})
    return {"run_id": "t", "t_s": 30.0, "tick": 60, "trigger": {"kind": "hazard_seen", "ref": "p4"},
            "agent": {"id": aid, "persona": persona, "room": "L2_office", "level": "L2", "known_exits": ["exit_01"],
                      "traits": {"t_since_cue_s": 30, "premove_s": 20}},
            "percepts": ps, "beliefs": [],
            "options": [{"id": "o1", "action": "TAKE_STAIRS", "stair": "st_east", "label": "East Stair",
                         "direction": "down", "route_m": 6.0, "queue_s": 2, "leads_to_level": "L1", "blocked": False},
                        {"id": "o2", "action": "TAKE_STAIRS", "stair": "st_west", "label": "West Stair",
                         "direction": "down", "route_m": 20.0, "queue_s": 0, "leads_to_level": "L1", "blocked": False},
                        {"id": "o3", "action": "TAKE_STAIRS", "stair": "st_up", "direction": "up", "route_m": 3.0,
                         "queue_s": 0, "leads_to_level": "L3", "blocked": False},
                        {"id": "o4", "action": "USE_ELEVATOR", "elevator": "el_1", "usable_in_fire": False},
                        {"id": "o5", "action": "WAIT_REFUGE", "ref": "st_east"},
                        {"id": "o6", "action": "SEEK_INFO"}]}


def test_fallback_takes_nearest_clear_stair_down():
    D.configure(offline=True)
    assert D.decide(stair_view())["choice"] == "o1"
    assert D.decide(stair_view(smoky_east=True, aid="a_51"))["choice"] == "o2"


def test_mobility_impaired_waits_at_refuge_and_asks_for_help():
    D.configure(offline=True)
    r = D.decide(stair_view(persona="mobility_impaired"))
    assert r["choice"] == "o5" and "help" in (r["say"] or "")


def test_validator_enforces_stair_and_elevator_rules():
    v = stair_view()
    ids = {"p1", "p2", "p3", "p4", "o1", "o2", "o3", "o4", "o5", "o6"}

    def pick(choice):
        return raw(choice=choice, options_considered=[choice], threat_evidence=["p4"], need_evidence=["p4"],
                   choice_evidence=["p4"])
    assert D.validate(pick("o1"), ids, v["options"], "regular_employee", True)[0] is not None
    assert D.validate(pick("o4"), ids, v["options"], "regular_employee", True)[0] is None  # lift in a fire
    assert D.validate(pick("o1"), ids, v["options"], "mobility_impaired", True)[0] is None


def test_smoky_stairwell_blocks_goal_and_raises_fear():
    st = lambda v: D.compute_state(D.build_inputs(v))  # noqa: E731
    clear, smoky = st(stair_view(fire=False)), st(stair_view(fire=False, smoky_east=True, aid="a_52"))
    assert smoky["appraisal"]["goal_blocked"] > clear["appraisal"]["goal_blocked"]
    assert smoky["emotion"]["fear"] > clear["emotion"]["fear"]
    assert any(b["key"] == "stair_ok:st_east" and b["value"] is False for b in smoky["beliefs_after"])


def test_prompt_explains_stairs_and_floor():
    inp = D.build_inputs(stair_view())
    msgs = D.prompt_for(inp, D.compute_state(inp))
    assert "TAKE_STAIRS" in msgs[0]["content"] and "usable_in_fire" in msgs[0]["content"]
    assert "on floor L2" in msgs[1]["content"] and "leads_to_level=L1" in msgs[1]["content"]


def test_interview_grounds_stair_names(tmp_path, monkeypatch):
    def stair_llm(messages, **kw):
        if messages[0]["content"] == COMPOSE_SYSTEM:
            tags = re.findall(r"\[t=[^\]]+\]", messages[1]["content"])
            return json.dumps({"sentences": [f"I took the east stairs. {tags[0]}"]}), \
                {"cached": False, "latency_ms": 1, "cost_usd": 0.0}
        user = messages[1]["content"]
        choice = "o2" if re.search(r"stair \(ref=st_east[^)]*smoke=True", user) else "o1"
        ev = ["p2"] if "- p2 " in user else ["p1"]
        return json.dumps({"padm": {"threat_real": "yes", "threat_evidence": ev, "need_action": "yes",
                                    "need_evidence": ev, "options_considered": ["o1", "o2"], "choice": choice,
                                    "why": "nearest stair down", "choice_evidence": ev, "when": "now"},
                           "say": None, "feel": "afraid", "confidence": 0.7}), \
            {"cached": False, "latency_ms": 1, "cost_usd": 0.0}
    monkeypatch.setattr(llm, "chat", stair_llm)
    rec = D.decide(stair_view())["record"]
    assert rec["source"] == "llm" and rec["decision"]["padm"]["choice"] == "o1"
    run = tmp_path / "r"
    run.mkdir()
    (run / "decisions.jsonl").write_text(json.dumps(rec) + "\n")
    ans = interview(run, "a_50", "Why did you take the east stairs?")
    assert ans["record_id"] == rec["record_id"] and ans["intent"] == "why"
    assert "east stairs" in ans["answer"]
    cf = interview(run, "a_50", "What if the east stairs had smoke in them?")
    assert cf["counterfactual"]["change"].startswith("you saw st_east")


def test_parse_json_ignores_trailing_junk():
    assert llm.parse_json('```json\n{"a": 1}\n37]."]}') == {"a": 1}


def test_redundant_label_only_when_no_single_reason_is_necessary(tmp_path):
    from cognition.interview import Budget, attribute
    rec = D.decide(view())["record"]  # fake LLM: evacuates iff the smoke percept p2 is present
    reasons, base = attribute({**rec, "decision": {**rec["decision"], "padm": {
        **rec["decision"]["padm"], "choice_evidence": ["p2", "p1"]}}}, Budget(40))
    labels = {r["id"]: r["label"] for r in reasons}
    assert labels == {"p2": "necessary", "p1": "stated, not decisive"}


def pacing_view(persona="regular_employee", since=30.0, extra=(), premove=60.0, aid="a_70"):
    v = view(aid=aid, persona=persona, smoke=False)
    v["percepts"][0]["t_since_s"] = since
    v["percepts"] += list(extra)
    v["agent"]["traits"].update({"t_since_cue_s": since, "premove_s": premove})
    return v


def offered_actions(v):
    st = D.compute_state(D.build_inputs(v))
    return {o["action"] for o in st["offered"]}


def test_regular_employee_keeps_working_until_second_cue_with_no_fixed_clock():
    assert "CONTINUE_TASK" in offered_actions(pacing_view(since=30))
    assert "CONTINUE_TASK" in offered_actions(pacing_view(since=400, premove=600))  # long tail kept
    crowd = [{"id": "p9", "kind": "people_moving", "count": 3, "channel": "sight"}]
    assert "CONTINUE_TASK" not in offered_actions(pacing_view(since=30, extra=crowd))


def test_seek_info_at_most_twice_and_nothing_to_wait_for_after_deadline():
    D.MINDS["a_71"] = {"persona": "visitor", "waits": {"SEEK_INFO": 2}, "t_s": 0.0}
    assert "SEEK_INFO" not in offered_actions(pacing_view(persona="visitor", aid="a_71"))
    late = offered_actions(pacing_view(since=100, premove=60))  # deadline 1.5 x 60 s = 90 s
    assert not late & {"SEEK_INFO", "CONTINUE_TASK"} and "EVACUATE" in late


def test_waits_are_counted_across_decisions():
    D.configure(offline=True)
    v = pacing_view(persona="skeptic", aid="a_72", premove=600)
    for t in range(3):
        D.decide({**v, "tick": 100 + 10 * t})
    assert sum(D.MINDS["a_72"]["waits"].values()) == 3


def test_ringing_alarm_and_people_leaving_raise_threat_certainty():
    s = lambda v: D.compute_state(D.build_inputs(v))["appraisal"]["certainty"]  # noqa: E731
    crowd = [{"id": "p9", "kind": "people_moving", "count": 4, "channel": "sight"}]
    assert s(pacing_view(since=5)) < s(pacing_view(since=90)) < s(pacing_view(since=90, extra=crowd))


def test_prompt_lists_accumulated_cues():
    inp = D.build_inputs(pacing_view(since=100, premove=60))
    user = D.prompt_for(inp, D.compute_state(inp))[1]["content"]
    assert "CUES SO FAR: alarm sounding for 100 s" in user and "you act now" in user
    assert "CONTINUE_TASK" not in user.split("OPTIONS:")[1]
