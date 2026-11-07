"""decide(view) for the sim (docs/SIM_INTERFACES.md): perceive, beliefs, appraise, deliberate, validate.

Per-agent memory (emotion, beliefs, last appraisal) lives in MINDS in this process; every
decision also stores that memory in its record, so a record can be re-run on its own.
Config through configure() or env: NOVA_NO_LLM=1 (fallback only), NOVA_REPLAY=<decisions.jsonl>.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import llm
from .appraisal import EVAC_ACTIONS, PROTECTIVE, HAZARD_KINDS, appraise, dominant, emotions, exit_open_prob
from .appraisal import _next_id, parse_claims, update_beliefs
from .personas import contagion_traits, persona
from .prompt import messages, user_message
from .schemas import Decision, DecisionRecord

_ENV_DONE = [False]
CONFIG = {"offline": os.environ.get("NOVA_NO_LLM") == "1", "run_id": "run", "model": llm.DEFAULT_MODEL,
          "temperature": 0.7, "include_emotion": True, "workers": 32, "timeout_s": 6.0}
MINDS: dict[str, dict] = {}
REPLAY: dict[tuple[int, str], dict] = {}
ATTN_K, ATTN_THETA = 0.5, 1.0  # COGNITION_SOURCES.md: k ~0.5 (ours); theta so a calm agent sees a static sign 38%
SALIENCE = {"exit_sign": 0.38, "person": 0.8, "exit": 0.9, "object": 0.5}
SPEECH = {"utterance", "said", "shout", "message"}
PRIORITY_KINDS = set(HAZARD_KINDS) | SPEECH | {"alarm", "pa", "exit", "exit_sign", "exit_blocked", "blocked",
                                              "people_moving"}
NEIGHBOUR_M, NEIGHBOUR_STALE_S, MAX_NEIGHBOURS = 6.0, 10.0, 8
FEEL_MAP = {"afraid": "fear", "scared": "fear", "terrified": "fear", "panicked": "fear", "frightened": "fear",
            "anxious": "anxiety", "worried": "anxiety", "nervous": "anxiety", "uneasy": "anxiety", "unsure": "anxiety",
            "confused": "anxiety", "frustrated": "frustration", "annoyed": "frustration", "irritated": "frustration",
            "hopeful": "hope", "determined": "hope", "focused": "hope", "relieved": "relief", "concerned": "concern",
            "calm": "calm", "fine": "calm", "skeptical": "calm", "unconcerned": "calm", "curious": "calm"}
BANNED = re.compile(r"https?://|www\.|\bas an ai\b|\bfuck|\bshit|\bbitch|\bkill (yourself|them)", re.I)


def configure(offline: bool | None = None, replay: str | Path | None = None, run_id: str | None = None,
              model: str | None = None, reset: bool = True, **kw) -> None:
    if reset:
        MINDS.clear()
        REPLAY.clear()
    if offline is not None:
        CONFIG["offline"] = offline
    if run_id:
        CONFIG["run_id"] = run_id
    if model:
        CONFIG["model"] = model
    CONFIG.update(kw)
    _ENV_DONE[0] = True
    env = os.environ.get("NOVA_REPLAY")
    if env == "1" and os.environ.get("NOVA_RUN_DIR"):
        env = str(Path(os.environ["NOVA_RUN_DIR"]) / "decisions.jsonl")
    replay = replay or env
    if replay and Path(replay).exists():
        for line in Path(replay).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                REPLAY[(r["run_id"], int(r["tick"]), r["agent_id"])] = r


def stats() -> dict:
    lat = sorted(llm.STATS["latency_ms"])
    return {"llm_calls": llm.STATS["calls"], "cached": llm.STATS["cached"], "errors": llm.STATS["errors"],
            "cost_usd": round(llm.STATS["cost_usd"], 6),
            "latency_ms_p50": lat[len(lat) // 2] if lat else None, "latency_ms_max": lat[-1] if lat else None}


# ---------- 1 perceive ----------

def _u01(*parts: object) -> float:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def perceive(percepts: list[dict], arousal: float, threatened: bool, seed: str) -> tuple[list[dict], list[str]]:
    """Attention reallocation (Easterbrook; Mather and Sutherland): under threat, arousal boosts
    threat, exit and route cues and suppresses peripheral ones. weight >= theta is always seen,
    otherwise seen with probability weight (deterministic per seed and percept id)."""
    kept, dropped = [], []
    for p in percepts:
        sal = float(p.get("salience", 0.77 if p.get("dynamic") else SALIENCE.get(p["kind"], 1.0)))
        prio = 1 if (p["kind"] in PRIORITY_KINDS or p.get("doing") in ("evacuating", "moving_to_exit")) else 0
        w = sal * (1 + ATTN_K * arousal * (2 * prio - 1)) if threatened else sal
        (kept if w >= ATTN_THETA or _u01(seed, p["id"]) < w else dropped).append(p)
    return kept, [p["id"] for p in dropped]


def _source_kind(p: dict, agent: dict) -> str:
    if p.get("source_kind"):
        return p["source_kind"]
    if p.get("kind") == "pa":
        return "pa"
    who = p.get("from")
    known = set((agent.get("traits") or {}).get("known", [])) | set((agent.get("traits") or {}).get("companions", []))
    other = MINDS.get(who, {}).get("persona") or p.get("speaker_persona")
    if other and persona(other)[0] == "warden":
        return "warden"
    if who in known or (other and persona(other)[0] == persona(agent.get("persona", ""))[0] == "regular_employee"):
        return "known"
    return "stranger"


def build_inputs(view: dict) -> dict:
    """Everything a decision depends on, as plain data (stored in the record for re-runs)."""
    agent = dict(view["agent"])
    aid, tick = agent["id"], int(view["tick"])
    run_id = view.get("run_id") or CONFIG["run_id"]
    mind = MINDS.get(aid, {})
    exits = {o["exit"]: o.get("label") or "" for o in view.get("options") or [] if o.get("exit")}
    exits.update({e: exits.get(e, "") for e in agent.get("known_exits") or []})
    raw = []
    for i, p in enumerate(view.get("percepts") or []):
        p = {"id": p.get("id") or f"p{i + 1}", "channel": p.get("channel", "sight"), **p}
        if p["kind"] in SPEECH:
            p["source_kind"] = _source_kind(p, agent)
            p.setdefault("claim", parse_claims(p.get("text", ""), exits))
        raw.append(p)
    threatened = mind.get("certainty", 0.0) > 0.2 or any(p["kind"] in HAZARD_KINDS for p in raw)
    percepts, dropped = perceive(raw, (mind.get("emotion") or {}).get("arousal", 0.0), threatened,
                                 f"{run_id}|{aid}|{tick}")
    neighbours = []

    def add_nb(who: str, pid: str, dist: float, channel: str) -> None:
        m = MINDS.get(who)
        if m and m.get("emotion") and who != aid and all(n["agent"] != who for n in neighbours):
            neighbours.append({"agent": who, "pid": pid, "dist_m": round(dist, 2), "channel": channel,
                               "fear": m["emotion"]["fear"], "anxiety": m["emotion"]["anxiety"],
                               "eps": contagion_traits(m["persona"])["eps"]})
    for p in percepts:
        if p["kind"] == "person" and p.get("ref"):
            add_nb(p["ref"], p["id"], float(p.get("dist_m") or 0.0), "sight")
        elif p["kind"] in SPEECH and p.get("from"):
            add_nb(p["from"], p["id"], float(p.get("dist_m") or 2.0), "hearing")
    crowd = next((p for p in percepts if p["kind"] == "people_moving"), None)
    if crowd and agent.get("pos"):
        # ponytail: the sim reports movers as one aggregate, so sight contagion uses each neighbour's
        # position at its last decision (<= 10 s old, no line-of-sight check); per-person percepts fix this
        for who, m in sorted(MINDS.items()):
            if m.get("pos") and float(view["t_s"]) - m["t_s"] <= NEIGHBOUR_STALE_S:
                d = ((m["pos"][0] - agent["pos"][0]) ** 2 + (m["pos"][1] - agent["pos"][1]) ** 2) ** 0.5
                if d <= NEIGHBOUR_M:
                    add_nb(who, crowd["id"], d, "sight")
    neighbours = sorted(neighbours, key=lambda n: n["dist_m"])[:MAX_NEIGHBOURS]
    prior = {b["key"]: dict(b) for b in mind.get("beliefs", [])}
    for b in view.get("beliefs") or []:  # sim-held beliefs fill gaps under fresh ids; ours win
        if b["key"] not in prior:
            nb = {**b, "id": _next_id(list(prior.values()))}
            nb.setdefault("conf", 0.5)
            nb.setdefault("tick", tick)
            nb.setdefault("source", {"kind": "prior"})
            prior[b["key"]] = nb
    return {"run_id": run_id, "tick": tick, "t_s": float(view["t_s"]), "agent": agent,
            "trigger": view.get("trigger") or {"kind": "periodic"}, "percepts": percepts, "dropped": dropped,
            "beliefs_before": list(prior.values()), "options": list(view.get("options") or []),
            "mind_before": {k: mind[k] for k in ("emotion", "t_s", "harm", "certainty") if k in mind},
            "neighbours": neighbours}


# ---------- 2-3 beliefs, appraisal, emotion (pure) ----------

def compute_state(inp: dict, remove: frozenset = frozenset(), keep_only: set | None = None,
                  add_percepts: tuple = ()) -> dict:
    """Pure: inputs (optionally with evidence ids removed / only some kept / cues added) -> state.
    Ablations regenerate everything downstream from structured state, never by deleting prompt text."""
    def keep(i: str | None) -> bool:
        return i not in remove and (keep_only is None or i in keep_only)
    agent, name = inp["agent"], inp["agent"].get("persona", "")
    percepts = [p for p in inp["percepts"] if keep(p["id"])] + list(add_percepts)
    before = [b for b in inp["beliefs_before"] if keep(b["id"])]
    after = update_beliefs(before, percepts, name, inp["tick"])
    after = [b for b in after if keep(b["id"]) or b["source"].get("ref") in {p["id"] for p in percepts}]
    pids = {p["id"] for p in percepts}
    neighbours = [n for n in inp["neighbours"] if n["pid"] in pids]
    traits = {**(agent.get("traits") or {}), "target_exit": agent.get("target_exit")}
    a = appraise(percepts, after, inp["options"], name, inp["trigger"], traits)
    emo, src = emotions(a, inp["mind_before"], inp["t_s"], name, neighbours)
    ids = pids | {b["id"] for b in after} | {o["id"] for o in inp["options"]}
    return {"percepts": percepts, "beliefs_after": after, "appraisal": a, "emotion": emo, "emotion_sources": src,
            "ids": ids}


def prompt_for(inp: dict, st: dict, include_emotion: bool | None = None) -> list[dict]:
    inc = CONFIG["include_emotion"] if include_emotion is None else include_emotion
    return messages(user_message(inp["agent"], inp["t_s"], inp["trigger"], st["percepts"], st["beliefs_after"],
                                 inp["options"], st["appraisal"], st["emotion"] if inc else None))


# ---------- 6 validate ----------

def check_say(say: str | None) -> list[str]:
    if say is None or not say.strip():
        return []
    errs = []
    if len(say.split()) > 12 or len(say) > 90:
        errs.append("say: longer than 12 words")
    if BANNED.search(say):
        errs.append("say: failed content filter")
    return errs


def validate(raw: str, ids: set, options: list[dict]) -> tuple[dict | None, list[str]]:
    try:
        d = Decision.model_validate(llm.parse_json(raw))
    except Exception as e:  # bad JSON or schema: report, caller repairs once
        return None, [f"schema: {str(e)[:240]}"]
    pd, errs = d.padm, []
    offered = {o["id"]: o for o in options}
    if pd.choice not in offered:
        errs.append(f"choice {pd.choice} is not an offered option ({', '.join(offered)})")
    unknown = sorted({i for i in pd.threat_evidence + pd.need_evidence + pd.choice_evidence if i not in ids})
    if unknown:
        errs.append(f"unknown evidence ids: {unknown}")
    if not pd.choice_evidence:
        errs.append("choice_evidence is empty")
    if pd.choice not in pd.options_considered:
        errs.append("options_considered must contain the choice")
    if any(o not in offered for o in pd.options_considered):
        errs.append("options_considered has ids that are not offered")
    action = offered.get(pd.choice, {}).get("action")
    if pd.need_action == "no" and action in PROTECTIVE:
        errs.append(f"need_action is 'no' but choice is {action}")
    if action == "CONTINUE_TASK" and pd.when == "now":
        errs.append("CONTINUE_TASK needs when = after_task or after_info")
    errs += check_say(d.say)
    out = d.model_dump()
    if out["say"] is not None and not out["say"].strip():
        out["say"] = None
    return (out if not errs else None), errs


# ---------- fallback ----------

def _evac_cost(o: dict, beliefs: list[dict], traits: dict) -> float:
    familiar = o.get("exit") in {traits.get("entry_exit"), traits.get("familiar_exit")} - {None}
    return (float(o.get("route_m") or 20) / 1.3 + float(o.get("queue_s") or 0) - (30 if familiar else 0)
            + 300 * (1 - exit_open_prob(beliefs, o.get("exit"))))


def fallback(inp: dict, st: dict) -> dict:
    """Deterministic persona policy. Same state in, same decision out."""
    name, p = persona(inp["agent"].get("persona", ""))
    traits = {"entry_exit": inp["agent"].get("entry_exit"), **(inp["agent"].get("traits") or {})}
    a, opts, beliefs = st["appraisal"], inp["options"], st["beliefs_after"]
    by = lambda act: [o for o in opts if o.get("action") == act]  # noqa: E731
    cues = [b["id"] for b in beliefs if b["value"] and not b["key"].startswith("exit_open")]
    seen = [x["id"] for x in st["percepts"] if x["kind"] in HAZARD_KINDS]
    speakers = {x.get("from") for x in st["percepts"]
                if x["kind"] in SPEECH and (x.get("urgent") or (x.get("claim") or {}).get("told_to_leave"))}
    waited = (inp["trigger"].get("kind") == "premove_done" or
              float(traits.get("t_since_cue_s") or 0) >= float(traits.get("premove_s") or 1e9))
    act = (a["certainty"] >= p["act_at"] or (name == "skeptic" and (seen or len(speakers) >= 2))
           or (waited and name != "skeptic") or (waited and a["certainty"] >= 0.5))
    if not act:
        pick = (by("CONTINUE_TASK") or by("SEEK_INFO") or opts)[0]
        need, when = ("later" if a["certainty"] > 0.1 else "no"), "after_info"
        if pick.get("action") in PROTECTIVE:
            need = "yes"
    else:
        need, when = "yes", "now"
        evac = sorted(by("EVACUATE"), key=lambda o: _evac_cost(o, beliefs, traits))
        if name == "parent_with_child" and by("GATHER"):
            pick = by("GATHER")[0]
        elif name == "warden" and by("HELP") and st["emotion"]["fear"] < 0.7:
            pick = by("HELP")[0]
        elif name in ("visitor", "older_adult") and by("FOLLOW") and a["social_proof"] > 0.3:
            pick = by("FOLLOW")[0]
        else:
            pick = (evac or by("FOLLOW") or by("HIDE") or by("SHELTER") or by("TURN_BACK") or by("SEEK_INFO")
                    or opts)[0]
    threat = seen + cues
    ev = (threat[:3] or [pick["id"]]) + ([pick["id"]] if threat else [])
    return {"padm": {"threat_real": "yes" if a["certainty"] >= 0.6 else ("unsure" if a["certainty"] > 0.1 else "no"),
                     "threat_evidence": threat[:3], "need_action": need, "need_evidence": threat[:2],
                     "options_considered": [o["id"] for o in opts[:4]] if pick in opts[:4] else
                     [pick["id"]] + [o["id"] for o in opts[:3]],
                     "choice": pick["id"], "why": f"Rule-based {name} policy: certainty {a['certainty']:.2f}.",
                     "choice_evidence": ev, "when": when},
            "say": p.get("say") if act and name == "warden" else None,
            "feel": dominant(st["emotion"]), "confidence": round(a["coping"], 2)}


# ---------- 5 deliberate ----------

def model_for(agent: dict) -> str:
    """The agent's OpenRouter model id, or the default when the sim gives a placeholder like 'rule-based'."""
    m = agent.get("model") or ""
    return m if "/" in m else CONFIG["model"]


def deliberate(inp: dict, st: dict, tag: str = "", temperature: float | None = None,
               include_emotion: bool | None = None) -> dict:
    """One LLM call, one repair, then fallback. Returns decision, raw, validation, source, latency, cost."""
    msgs = prompt_for(inp, st, include_emotion)
    sha = hashlib.sha256(json.dumps(msgs).encode()).hexdigest()
    out = {"prompt_sha256": sha, "raw_output": "", "latency_ms": 0, "cost_usd": 0.0}
    if not inp["options"] or CONFIG["offline"]:
        return {**out, "decision": fallback(inp, st), "validation": {"status": "fallback", "errors":
                ["offline" if CONFIG["offline"] else "no options"]}, "source": "fallback"}
    model = model_for(inp["agent"])
    temp = CONFIG["temperature"] if temperature is None else temperature
    errors: list[str] = []
    for attempt in range(2):
        try:
            raw, meta = llm.chat(msgs, model=model, temperature=temp, tag=tag, timeout=CONFIG["timeout_s"])
        except Exception as e:  # timeout, HTTP 402/429/5xx, no key: record and fall back
            errors.append(f"llm: {type(e).__name__}: {str(e)[:120]}")
            break
        out["latency_ms"] += meta["latency_ms"]
        out["cost_usd"] += meta["cost_usd"]
        out["raw_output"] = raw
        dec, errs = validate(raw, st["ids"], inp["options"])
        if dec:
            return {**out, "decision": dec, "source": "llm",
                    "validation": {"status": "repaired" if attempt else "ok", "errors": errors}}
        errors += errs
        msgs = msgs + [{"role": "assistant", "content": raw},
                       {"role": "user", "content": "That answer is invalid: " + "; ".join(errs) +
                        ". Return the corrected JSON object only."}]
    return {**out, "decision": fallback(inp, st), "validation": {"status": "fallback", "errors": errors},
            "source": "fallback"}


# ---------- 7 commit ----------

def _record(inp: dict, st: dict, res: dict) -> dict:
    agent = inp["agent"]
    feel = FEEL_MAP.get(str(res["decision"].get("feel", "")).lower().strip())
    rec = {"record_id": f"d_{inp['tick']:05d}_{agent['id']}", "run_id": inp["run_id"], "tick": inp["tick"],
           "t_s": inp["t_s"], "agent_id": agent["id"], "persona": persona(agent.get("persona", ""))[0],
           "model": model_for(agent), "trigger": inp["trigger"], "percepts": st["percepts"],
           "attention_dropped": inp["dropped"], "beliefs_before": inp["beliefs_before"],
           "beliefs_after": st["beliefs_after"], "appraisal": st["appraisal"], "emotion": st["emotion"],
           "emotion_sources": st["emotion_sources"], "options": inp["options"],
           "inputs": {"agent": agent, "mind_before": inp["mind_before"], "neighbours": inp["neighbours"],
                      "temperature": CONFIG["temperature"], "include_emotion": CONFIG["include_emotion"]},
           **res, "feel_agrees": None if feel is None else feel == dominant(st["emotion"])}
    return DecisionRecord.model_validate(rec).model_dump(mode="json", by_alias=True)


def inputs_from_record(rec: dict) -> dict:
    return {"run_id": rec["run_id"], "tick": rec["tick"], "t_s": rec["t_s"], "agent": rec["inputs"]["agent"],
            "trigger": rec["trigger"], "percepts": rec["percepts"], "dropped": rec["attention_dropped"],
            "beliefs_before": rec["beliefs_before"], "options": rec["options"],
            "mind_before": rec["inputs"]["mind_before"], "neighbours": rec["inputs"]["neighbours"]}


def _remember(rec: dict) -> None:
    MINDS[rec["agent_id"]] = {"persona": rec["persona"], "emotion": rec["emotion"], "t_s": rec["t_s"],
                              "pos": (rec.get("inputs") or {}).get("agent", {}).get("pos"),
                              "harm": rec["appraisal"]["harm"], "certainty": rec["appraisal"]["certainty"],
                              "beliefs": rec["beliefs_after"]}


def _result(rec: dict) -> dict:
    return {"choice": rec["decision"]["padm"]["choice"], "say": rec["decision"].get("say"), "record": rec}


def decide_batch(views: list[dict]) -> list[dict]:
    """Decide for many agents in one tick: state in order, LLM calls in parallel, minds updated after.
    Every agent in the batch sees its neighbours' emotions from before this batch (order-independent)."""
    if not _ENV_DONE[0]:
        configure(reset=False)
    out: list[dict | None] = [None] * len(views)
    jobs = []
    for i, v in enumerate(views):
        hit = REPLAY.get((v.get("run_id") or CONFIG["run_id"], int(v["tick"]), v["agent"]["id"]))
        if hit:
            out[i] = _result(hit)
            continue
        inp = build_inputs(v)
        jobs.append((i, inp, compute_state(inp)))
    if jobs:
        with ThreadPoolExecutor(max_workers=min(CONFIG["workers"], len(jobs))) as ex:
            results = list(ex.map(lambda j: deliberate(j[1], j[2]), jobs))
        for (i, inp, st), res in zip(jobs, results):
            out[i] = _result(_record(inp, st, res))
    for r in out:
        _remember(r["record"])
    return out


def decide(view: dict) -> dict:
    return decide_batch([view])[0]
