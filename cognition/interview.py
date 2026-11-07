"""Grounded interview of one simulated occupant (docs/AGENT_COGNITION.md, interview pipeline).

route intent -> ground entities -> retrieve records -> attribute reasons by re-running the
recorded decision with evidence removed (baseline, necessity, joint removal, sufficiency;
n samples each, cached) or apply a counterfactual -> compose in persona voice -> drop every
sentence that does not cite an existing record.
"""

from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from . import llm
from .decide import CONFIG, compute_state, fallback, inputs_from_record, prompt_for, validate
from .personas import persona
from .schemas import InterviewAnswer

MAX_CALLS = 40
N_SAMPLES = 10
MAX_CITED = 4
ALPHA = 0.1  # one-sided Fisher exact; ponytail: no multiple-comparison correction across <= 4 reasons
TAG_RE = re.compile(r"\[t=[^\]]+\]")
KIND_WORDS = {"smoke": {"smoke", "fire"}, "fire": {"fire", "smoke"}, "flame": {"fire"}, "alarm": {"alarm"},
              "sign": {"exit_sign"}, "told": {"utterance", "said", "shout", "pa"}, "said": {"utterance", "said", "shout"},
              "rumor": {"utterance", "said", "shout"}, "announcement": {"pa"}, "people": {"person"},
              "crowd": {"person"}, "others": {"person"}, "intruder": {"intruder"}, "gunshot": {"gunshot"}}
NEGATION = re.compile(r"\b(no|not|hadn't|had not|didn't|did not|without|never|wasn't|weren't|nobody|no one)\b", re.I)


# ---------- load and retrieve ----------

@lru_cache(maxsize=16)
def _load(run_dir: str, mtime: float) -> dict:
    d = Path(run_dir)
    recs = [json.loads(x) for x in (d / "decisions.jsonl").read_text().splitlines() if x.strip()]
    replay = json.loads((d / "replay.json").read_text()) if (d / "replay.json").exists() else {}
    scene: dict = {}
    sp = (replay.get("meta") or {}).get("scene")
    for cand in (Path(sp) if sp else None, d / "scene.json"):
        if cand and cand.exists():
            scene = json.loads(cand.read_text())
            break
    return {"records": recs, "events": replay.get("events", []), "scene": scene}


def load_run(run_dir: Path) -> dict:
    f = run_dir / "decisions.jsonl"
    if not f.exists():
        raise FileNotFoundError(f"no decisions.jsonl in {run_dir.name}")
    return _load(str(run_dir), f.stat().st_mtime)


# ---------- route and ground ----------

def route(q: str) -> str:
    ql = q.lower()
    if re.search(r"what if|would you|\bif (you|the|there|nobody|no one|exit|someone)\b|suppose|had you", ql):
        return "what_if"
    if re.search(r"who (told|said)|heard from|did (anyone|someone) (tell|say)|who warned", ql):
        return "who_told"
    if re.search(r"\b(feel|felt|scared|afraid|emotion|nervous|panic)", ql):
        return "felt"
    if re.search(r"what did you (see|hear|notice)|\b(saw|notice)", ql):
        return "saw"
    if re.search(r"what happened|walk me through|timeline", ql):
        return "what_happened"
    return "why"


def ground(q: str, scene: dict) -> dict:
    """Scene labels and ids mentioned in the question, and a time if one is given."""
    ql = q.lower()
    ents = []
    for kind in ("exits", "rooms", "doors"):
        for e in scene.get(kind, []):
            names = {e["id"].lower(), str(e.get("label") or "").lower(), str(e.get("name") or "").lower()} - {""}
            if any(re.search(rf"\b{re.escape(n)}\b", ql) for n in names):
                ents.append({"kind": kind[:-1], "id": e["id"]})
    for m in re.finditer(r"\bexit\s*(\d+)\b|\be_0*(\d+)\b", ql):  # works without a scene too
        eid = f"e_{int(m.group(1) or m.group(2)):03d}"
        if not any(x["id"] == eid for x in ents):
            ents.append({"kind": "exit", "id": eid})
    ents += [{"kind": "agent", "id": a} for a in re.findall(r"\ba_\d+\b", ql)]
    t = None
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds)\b", ql):
        t = float(m.group(1))
    elif m := re.search(r"(\d+(?:\.\d+)?)\s*(min|minute|minutes)\b", ql):
        t = float(m.group(1)) * 60
    when = "first" if re.search(r"\b(first|start|beginning|initially)\b", ql) else \
        "last" if re.search(r"\b(last|finally|end)\b", ql) else None
    return {"entities": ents, "t_s": t, "when": when}


def chosen(rec: dict) -> dict:
    c = rec["decision"]["padm"]["choice"]
    return next((o for o in rec["options"] if o["id"] == c), {"id": c, "action": "?"})


def pick_record(recs: list[dict], g: dict) -> dict:
    if g["t_s"] is not None:
        before = [r for r in recs if r["t_s"] <= g["t_s"] + 0.5]
        return before[-1] if before else recs[0]
    for e in g["entities"]:
        hit = [r for r in recs if e["id"] in json.dumps(chosen(r))]
        if hit:
            return hit[0]
    if g["when"] == "first":
        return recs[0]
    if g["when"] == "last":
        return recs[-1]
    acted = [r for r in recs if chosen(r).get("action") not in ("CONTINUE_TASK", "SEEK_INFO", "?")]
    return acted[0] if acted else recs[-1]


# ---------- facts with citations ----------

def tag(rec: dict, item: str | None = None) -> str:
    return f"[t={rec['t_s']:g}, {rec['record_id']}{':' + item if item else ''}]"


def describe_option(o: dict) -> str:
    extra = ", ".join(f"{k} {v}" for k, v in o.items() if k not in ("id", "action") and v is not None)
    return f"{o['action']} ({extra})" if extra else o["action"]


def facts_for(rec: dict, recs: list[dict], events: list[dict], intent: str) -> list[tuple[str, str]]:
    """(fact text, citation tag). The composer may only use these."""
    out = [(f"At t={rec['t_s']:g} s ({rec['trigger'].get('kind')}) I chose {describe_option(chosen(rec))}.", tag(rec)),
           (f"My stated reason at the time: \"{rec['decision']['padm']['why']}\"", tag(rec))]
    for p in rec["percepts"]:
        bits = {k: v for k, v in p.items() if k not in ("id", "channel") and v is not None}
        out.append((f"I perceived ({p.get('channel')}): {json.dumps(bits)}", tag(rec, p["id"])))
    if rec.get("attention_dropped"):
        out.append((f"Cues present that I did not notice: {', '.join(rec['attention_dropped'])}", tag(rec)))
    for b in rec["beliefs_after"]:
        s = b["source"]
        out.append((f"I believed {b['key']} = {b['value']} (confidence {b['conf']:.2f}, source {s['kind']}"
                     f"{' from ' + s['from'] if s.get('from') else ''}, since tick {b['tick']})", tag(rec, b["id"])))
    e = rec["emotion"]
    out.append(("Computed feelings: " + ", ".join(f"{k} {e[k]:.2f}" for k in
                ("fear", "anxiety", "frustration", "hope", "relief", "concern")) +
                f"; I said I felt '{rec['decision'].get('feel')}'.", tag(rec)))
    for s in rec["emotion_sources"]:
        if s.get("from") == "contagion" and abs(s.get("delta", 0)) >= 0.01:
            out.append((f"My {s['emotion']} changed by {s['delta']:+.2f} from being near {s['agent']}.", tag(rec)))
    if rec["decision"].get("say"):
        out.append((f"I said: \"{rec['decision']['say']}\"", tag(rec)))
    window = recs if intent == "what_happened" else [r for r in recs if r is not rec][:6]
    for r in window:
        out.append((f"At t={r['t_s']:g} s I chose {describe_option(chosen(r))}.", tag(r)))
    for ev in events[:12]:
        out.append((f"Event at t={ev.get('t')}: {ev.get('kind')} {ev.get('text') or ev.get('ref') or ''}".strip(),
                    f"[t={ev.get('t')}, event:{ev.get('kind')}]"))
    return out


# ---------- re-runs ----------

class Budget:
    def __init__(self, cap: int) -> None:
        self.left, self.calls, self.cost = cap, 0, 0.0

    def take(self, k: int) -> int:
        k = max(0, min(k, self.left))
        self.left -= k
        return k


def _sample(inp: dict, st: dict, i: int, rec: dict, b: Budget) -> str | None:
    """One re-run sample: the option id chosen, or None if invalid / failed."""
    if rec["source"] == "fallback":
        return fallback(inp, st)["padm"]["choice"]
    msgs = prompt_for(inp, st, rec["inputs"].get("include_emotion", True))
    try:
        raw, meta = llm.chat(msgs, model=rec["model"], temperature=rec["inputs"].get("temperature", 0.7),
                             tag=f"interview-s{i}", timeout=CONFIG["timeout_s"])
    except Exception:  # count as an invalid sample; the result reports n_valid
        return None
    b.calls += 0 if meta["cached"] else 1
    b.cost += meta["cost_usd"]
    dec, _ = validate(raw, st["ids"], inp["options"])
    return dec["padm"]["choice"] if dec else None


def run_conditions(rec: dict, conds: dict[str, dict], n: int, b: Budget) -> dict[str, list]:
    inp = inputs_from_record(rec)
    states = {k: compute_state(inp, **kw) for k, kw in conds.items()}
    per = 1 if rec["source"] == "fallback" else n  # the fallback is deterministic: one sample is exact
    jobs = []
    for k in conds:
        start = 1 if k == "baseline" and rec["source"] == "llm" else 0  # sample 0 is the recorded decision
        for i in range(start, start + b.take(per - start)):
            jobs.append((k, i))
    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(lambda j: (j[0], _sample(inp, states[j[0]], j[1], rec, b)), jobs))
    out: dict[str, list] = {k: [] for k in conds}
    if "baseline" in conds and rec["source"] == "llm":
        out["baseline"].append(rec["decision"]["padm"]["choice"])
    for k, c in res:
        out[k].append(c)
    return out


def fisher_lower(k1: int, n1: int, k0: int, n0: int) -> float:
    """One-sided p that the keep rate k1/n1 is below k0/n0 (hypergeometric lower tail)."""
    big_k, big_n = k1 + k0, n1 + n0
    if n1 == 0 or n0 == 0:
        return 1.0
    total = math.comb(big_n, n1)
    return sum(math.comb(big_k, x) * math.comb(big_n - big_k, n1 - x) for x in range(0, k1 + 1)
               if 0 <= n1 - x <= big_n - big_k) / total


def _rate(xs: list, choice: str) -> tuple[int, int]:
    return sum(1 for x in xs if x == choice), len(xs)


def cited_items(rec: dict) -> list[str]:
    pd = rec["decision"]["padm"]
    have = {p["id"] for p in rec["percepts"]} | {b["id"] for b in rec["beliefs_after"]}
    seen: list[str] = []
    for i in pd["choice_evidence"] + pd["threat_evidence"] + pd.get("need_evidence", []):
        if i in have and i not in seen:
            seen.append(i)
    return seen[:MAX_CITED]


def attribute(rec: dict, b: Budget) -> tuple[list[dict], dict]:
    cited = cited_items(rec)
    conds: dict[str, dict] = {"baseline": {}}
    conds.update({f"rm:{i}": {"remove": frozenset({i})} for i in cited})
    if len(cited) > 1:
        conds["joint"] = {"remove": frozenset(cited)}
        conds["keep_only"] = {"keep_only": set(cited)}
    n = min(N_SAMPLES, max(3, b.left // len(conds)))
    samples = run_conditions(rec, conds, n, b)
    choice = rec["decision"]["padm"]["choice"]
    k0, n0 = _rate(samples["baseline"], choice)
    joint = samples.get("joint", samples.get(f"rm:{cited[0]}") if cited else [])
    kj, nj = _rate(joint, choice) if cited else (0, 0)
    ks, ns = _rate(samples.get("keep_only", samples["baseline"]), choice)
    joint_sig = bool(cited) and fisher_lower(kj, nj, k0, n0) < ALPHA
    sufficient = ns > 0 and fisher_lower(ks, ns, k0, n0) >= ALPHA and ks / ns >= 0.5
    text = {**{p["id"]: f"{p['kind']} {p.get('where') or p.get('ref') or p.get('text') or ''}".strip()
               for p in rec["percepts"]},
            **{x["id"]: f"belief {x['key']} = {x['value']}" for x in rec["beliefs_after"]}}
    reasons = []
    for i in cited:
        k1, n1 = _rate(samples[f"rm:{i}"], choice)
        if fisher_lower(k1, n1, k0, n0) < ALPHA:
            label = "necessary"
        elif joint_sig and sufficient:
            label = "jointly sufficient"
        else:
            label = "stated, not decisive"
        reasons.append({"id": i, "text": text.get(i, i), "label": label,
                        "keep_rate_removed": round(k1 / n1, 2) if n1 else None, "n": n1})
    base = {"choice": choice, "keep_rate": round(k0 / n0, 2) if n0 else None, "n": n0,
            "joint_keep_rate": round(kj / nj, 2) if nj else None,
            "keep_only_cited_rate": round(ks / ns, 2) if ns else None, "sufficient": sufficient,
            "samples": {k: v for k, v in samples.items()}}
    return reasons, base


def parse_change(q: str, rec: dict, g: dict) -> tuple[dict, str]:
    """Question text -> compute_state() kwargs. ponytail: keyword rules; an LLM parser if users need more."""
    ql, neg = q.lower(), bool(NEGATION.search(q))
    exits = [e["id"] for e in g["entities"] if e["kind"] == "exit"]
    room = rec["inputs"]["agent"].get("room")
    if exits and re.search(r"block|lock|closed|shut", ql) and not neg:
        return {"add_percepts": ({"id": "cf1", "kind": "exit_blocked", "ref": exits[0], "channel": "sight"},)}, \
            f"you saw {exits[0]} blocked"
    if exits and re.search(r"\bopen|clear|unblocked", ql):
        return {"add_percepts": ({"id": "cf1", "kind": "exit", "ref": exits[0], "blocked": False,
                                  "channel": "sight"},)}, f"you saw {exits[0]} open"
    for w, kinds in KIND_WORDS.items():
        if re.search(rf"\b{w}", ql):
            have = [p["id"] for p in rec["percepts"] if p["kind"] in kinds]
            if neg or (have and not re.search(r"\b(more|again|also)\b", ql)):
                return {"remove": frozenset(have)}, f"without {w} ({', '.join(have) or 'none present'})"
            kind = sorted(kinds)[0]
            add = {"id": "cf1", "kind": kind, "channel": "sight", "where": room, "dist_m": 5.0}
            if kind == "exit_sign":
                add = {"id": "cf1", "kind": "exit_sign", "ref": (exits or [None])[0], "lit": True, "dist_m": 6.0}
            return {"add_percepts": (add,)}, f"with {kind} added"
    return {}, "no change I could map to the recorded state"


# ---------- compose and verify ----------

COMPOSE_SYSTEM = """You are a person who was inside a building during an emergency, now answering an investigator.
Speak in the first person, in plain everyday words, as the person described. Use ONLY the FACTS given; do not add
events, places, people or numbers that are not in them. Every sentence must end with one or more citation tags
copied exactly from the facts, like [t=78, d_00156_a_17:p3]. Write 2 to 5 short sentences that answer the question.
Return JSON only: {"sentences": ["...", "..."]}"""


def _parse_tag(t: str) -> dict:
    m = re.match(r"\[t=([^,]+), ([^:\]]+)(?::([^\]]+))?\]", t)
    return {"t": m.group(1), "record_id": m.group(2), "item": m.group(3)} if m else {}


def verify(sentences: list[str], allowed: set[str]) -> tuple[list[str], list[str]]:
    keep, drop = [], []
    for s in sentences:
        tags = TAG_RE.findall(s)
        (keep if tags and all(t in allowed for t in tags) else drop).append(s.strip())
    return keep, drop


def compose(question: str, rec: dict, facts: list[tuple[str, str]], extra: list[str], b: Budget) -> list[str]:
    name, p = persona(rec["persona"])
    user = (f"PERSON: {name}. {p['text']}\nQUESTION: {question}\nFACTS:\n" +
            "\n".join(f"- {t} {c}" for t, c in facts) + ("\nTEST RESULTS:\n" + "\n".join(extra) if extra else ""))
    if b.take(1):
        try:
            raw, meta = llm.chat([{"role": "system", "content": COMPOSE_SYSTEM}, {"role": "user", "content": user}],
                                 model=llm.DEFAULT_MODEL, temperature=0.3, max_tokens=400, tag="compose",
                                 timeout=10.0)
            b.calls += 0 if meta["cached"] else 1
            b.cost += meta["cost_usd"]
            out = llm.parse_json(raw).get("sentences") or []
            if isinstance(out, list) and all(isinstance(s, str) for s in out):
                return out
        except Exception:  # no key, timeout or bad JSON: use the template below
            pass
    return [f"I chose {describe_option(chosen(rec))} at {rec['t_s']:g} s. {tag(rec)}",
            f"At the time I said: \"{rec['decision']['padm']['why']}\" {tag(rec)}"]


def interview(run_dir: Path, agent_id: str, question: str, max_calls: int = MAX_CALLS) -> dict:
    run = load_run(run_dir)
    recs = sorted((r for r in run["records"] if r["agent_id"] == agent_id), key=lambda r: r["tick"])
    if not recs:
        raise KeyError(f"no decisions for agent {agent_id}")
    events = [e for e in run["events"] if e.get("agent") == agent_id]
    intent, g = route(question), ground(question, run["scene"])
    rec = pick_record(recs, g)
    b = Budget(max_calls - 1)  # one call kept for composing
    reasons, base, cf, extra = [], None, None, []
    choice = rec["decision"]["padm"]["choice"]
    if intent == "why":
        reasons, base = attribute(rec, b)
        for r in reasons:
            extra.append(f"- Without {r['id']} ({r['text']}) I kept my choice in {r['keep_rate_removed']} of re-runs "
                         f"(n={r['n']}); unchanged: {base['keep_rate']} (n={base['n']}); label: {r['label']} "
                         f"{tag(rec, r['id'])}")
    elif intent == "what_if":
        kw, desc = parse_change(question, rec, g)
        n = min(N_SAMPLES, max(3, b.left // 2))
        s = run_conditions(rec, {"baseline": {}, "cf": kw}, n, b) if kw else {"baseline": [choice], "cf": []}
        dist = {c or "invalid": s["cf"].count(c) for c in set(s["cf"])}
        cf = {"change": desc, "baseline": {c or "invalid": s["baseline"].count(c) for c in set(s["baseline"])},
              "counterfactual": dist, "n": len(s["cf"])}
        names = {o["id"]: describe_option(o) for o in rec["options"]}
        extra.append(f"- Re-run {len(s['cf'])} times if {desc}: " +
                     ", ".join(f"{names.get(k, k)} {v}x" for k, v in sorted(dist.items(), key=lambda x: -x[1])) +
                     f" (as recorded: {names.get(choice, choice)}) {tag(rec)}")
    facts = facts_for(rec, recs, events, intent)
    allowed = {c for _, c in facts} | {tag(rec, r["id"]) for r in reasons}
    sentences = compose(question, rec, facts, extra, b)
    keep, drop = verify(sentences, allowed)
    keep += [e[2:] for e in extra if e[2:] not in keep]  # code-made test results are true by construction
    cites = [{"tag": t, **_parse_tag(t)} for t in sorted({t for s in keep for t in TAG_RE.findall(s)})]
    ans = InterviewAnswer(answer=" ".join(keep), citations=cites, reasons=reasons,
                          verified=not drop and bool(keep), intent=intent, record_id=rec["record_id"],
                          dropped_sentences=drop, baseline=base, counterfactual=cf, llm_calls=b.calls,
                          cost_usd=round(b.cost, 6))
    return ans.model_dump()
