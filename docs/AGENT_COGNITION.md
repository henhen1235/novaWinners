# Occupant cognition, decision logging and interviews (design)

Status: design for WS-C, 2026-11-07. Citations marked [verify] are being
checked; see the Sources section.

## The problem with "log the decision, explain it later"

Asking a model afterwards why it did something produces a plausible story, not
the cause. Chain-of-thought explanations are often unfaithful to what actually
drove the output (Turpin et al. 2023), and people do the same thing when asked
about their own choices (Nisbett and Wilson 1977). So the interview cannot be
"here is the action, now explain it". Three rules follow:

1. Record state and stated reasons at decision time, never reconstruct them
   later (concurrent reports beat retrospective ones for people too: Ericsson
   and Simon 1980). This alone does NOT make reasons faithful: Turpin's
   unfaithful explanations were also produced at decision time. Rule 3 is what
   catches that.
2. Compute what research has numbers for in code (perception, trust, appraisal,
   emotion, contagion, timing); use the LLM only for judgment under ambiguity.
3. Test reasons by intervention (erasure / leave-one-out faithfulness tests:
   ERASER comprehensiveness, DeYoung et al. 2020; redaction checks, Madsen et
   al. 2024; counterfactual tests, Atanasova et al. 2023; causal concept
   faithfulness, Matton et al. 2025). Our contribution is applying them to
   simulated occupants, not the method itself.

## Pipeline (one decision, one occupant)

```mermaid
flowchart LR
  T[Trigger<br/>code] --> P[Perceive<br/>code]
  P --> B[Update beliefs<br/>code]
  B --> A[Appraise + emotion<br/>code]
  A --> O[Enumerate options<br/>code]
  O --> D[Deliberate PADM<br/>LLM, JSON]
  D --> V{Validate<br/>code}
  V -- ok --> C[Commit record<br/>event log]
  V -- invalid --> R[Repair once<br/>LLM] --> V
  V -- still invalid --> F[Fallback policy<br/>code] --> C
  C --> X[Act<br/>navmesh]
  A -. arousal narrows .-> P
  C -. utterances, emotion .-> N[Neighbours' perception<br/>contagion]
```

| Stage | Who | What it does | Grounding |
|---|---|---|---|
| 0 Trigger | code | Decision points: cue onset (alarm, smoke, PA, shout), fork reached, path blocked, someone needs help, periodic re-appraisal every 20 s | PADM: decisions are cue-driven (Lindell and Perry 2012) |
| 1 Perceive | code | Builds a Percept: what this occupant can see (isovist on the walkable area: exits, signs, smoke, people and what they do) and hear (alarm, PA, utterances within earshot). Attention budget shrinks as arousal rises | Easterbrook 1959 cue-utilization: high arousal narrows attention [verify]; static exit signs noticed by ~38% (Xie 2012, in research/MAPPING_IDEAS.md) |
| 2 Beliefs | code | Beliefs carry value, confidence, source and tick. Source credibility weights: own observation > official PA > warden/staff > known person > stranger. Direct observation overrides hearsay | Trust in warning sources (Lindell and Perry 2012, stakeholder perceptions); rumor handling for the adversary |
| 3 Appraise + emotion | code | Appraisal variables: relevance, threat certainty, urgency, coping potential, goal blocked, social proof (fraction of neighbours evacuating), responsibility (role, dependants). Emotions computed from appraisals: fear, anxiety, frustration, hope/relief, concern for others; plus valence and arousal. Decay to a personality baseline; contagion from neighbours | Appraisal theory (Lazarus 1991; Scherer component process model) [verify]; OCC model for computational emotions (Ortony, Clore, Collins 1988); ASCRIBE emotion contagion (Bosse et al.) [verify]; OCEAN-to-crowd parameters (Durupinar et al. 2016) [verify] |
| 4 Options | code | Closed set of feasible actions with features: EVACUATE(exit, route length, est. queue), FOLLOW(person), SEEK_INFO(person or place), HELP(person), GATHER(person or item), CONTINUE_TASK, HIDE(room) for intruder, SHELTER for hazmat, TURN_BACK. Only exits this occupant knows (entry route, seen, signed, told) | Familiar-exit bias and affiliation (Sime 1985); milling and information seeking (Wood, Mileti) [verify] |
| 5 Deliberate | LLM | One structured call: persona (archetype + OCEAN + role) + percept + beliefs + appraisal/emotion + options. Output walks the five PADM questions and must cite evidence ids | PADM protective-action questions (Lindell and Perry 2012) |
| 6 Validate | code | Option id is offered; every cited evidence id exists and was available to this occupant; PADM answers consistent with the choice; utterance passes a content filter. One repair try, then a deterministic persona fallback | "Model proposes, code checks" |
| 7 Commit | code | Append an immutable DecisionRecord to the event log (JSONL). The log is the single source of truth for replay, interviews and metrics | Event sourcing; flight-recorder principle |
| 8 Act | code | Navmesh steering; timing gates (notice latency sampled per persona, lognormal) | Pre-movement distributions (Lovreglio et al. 2019 database) |

Why emotions are computed, not asked for: an LLM asked "how do you feel" is
inconsistent and too calm (research/SCENARIOS_AND_MODELS.md: the anxious persona
froze in 22 of 24 cornered calls vs 34% in real footage). Computing emotion from
appraisals makes it reproducible, tunable to data, and explainable ("fear 0.8:
smoke visible, nearest known exit blocked"). The LLM also returns a one-word
self-report; we log both and report how often they agree.

The feedback loop is the interesting science: arousal narrows perception, fewer
cues lower coping potential, which raises fear, which spreads to neighbours.
Each link is a published effect and each is a separate, testable function.

## Deliberation output (what the LLM must return)

```json
{
  "padm": {
    "threat_real": "yes|no|unsure", "threat_evidence": ["p3", "b2"],
    "need_action": "yes|no|later",  "need_evidence": ["p3"],
    "options_considered": ["o1", "o4"],
    "choice": "o4", "why": "Smoke at the main hall; the side exit sign is lit.",
    "choice_evidence": ["p3", "p7"],
    "when": "now|after_task|after_info"
  },
  "say": "Side exit, follow me",
  "feel": "afraid", "confidence": 0.7
}
```

Evidence ids (`p*` percepts, `b*` beliefs, `m*` messages, `o*` options) are
handed to the model in the prompt, so every reason is a pointer the validator
can check, not free text.

## DecisionRecord (one line of the event log)

```json
{
  "record_id": "d_000184", "run_id": "run_42", "tick": 312, "t_s": 78.0,
  "agent_id": "a_17", "persona": "first_time_visitor", "model": "google/gemini-2.5-flash-lite",
  "trigger": {"kind": "fork_reached", "ref": "node_33"},
  "percepts": [{"id": "p3", "kind": "smoke", "where": "r_lobby", "dist_m": 6.2, "channel": "sight"},
               {"id": "p7", "kind": "exit_sign", "ref": "e_2", "lit": true, "dist_m": 9.0}],
  "beliefs_before": [{"id": "b2", "key": "exit_open:e_1", "value": false, "conf": 0.6,
                      "source": {"kind": "utterance", "ref": "m_88", "from": "a_09"}, "tick": 290}],
  "beliefs_after": "...",
  "appraisal": {"relevance": 0.9, "certainty": 0.8, "urgency": 0.7, "coping": 0.4,
                "goal_blocked": 0.3, "social_proof": 0.5, "responsibility": 0.0},
  "emotion": {"fear": 0.72, "anxiety": 0.31, "frustration": 0.22, "hope": 0.35,
              "concern": 0.05, "valence": -0.58, "arousal": 0.81},
  "emotion_sources": [{"emotion": "fear", "from": "appraisal", "delta": 0.5},
                      {"emotion": "fear", "from": "contagion", "agent": "a_09", "delta": 0.12}],
  "options": [{"id": "o1", "action": "EVACUATE", "exit": "e_1", "route_m": 14.0, "queue_s": 20},
              {"id": "o4", "action": "EVACUATE", "exit": "e_2", "route_m": 22.0, "queue_s": 0}],
  "prompt_sha256": "...", "raw_output": "...", "decision": {"...": "PADM block above"},
  "validation": {"status": "ok|repaired|fallback", "errors": []},
  "latency_ms": 612, "cost_usd": 0.00011
}
```

Between decisions the log also records episodic events (saw, heard, said,
arrived, blocked, helped), so the interview can answer "what happened" as well
as "why".

## Interview pipeline (agentic, grounded, checked)

```mermaid
flowchart LR
  Q[Question] --> RT[Route intent<br/>why / saw / felt / who told / what if]
  RT --> G[Ground entities<br/>scene labels to ids, phrases to ticks]
  G --> RE[Retrieve<br/>this agent's records + events]
  RE --> AB[Attribute reasons<br/>ablate each cited evidence, re-run x3]
  RE --> CF[Counterfactual<br/>re-run with the user's change]
  AB --> W[Compose answer<br/>persona voice, facts only from records]
  CF --> W
  W --> VF{Verify claims<br/>citation exists + supported}
  VF -- pass --> OUT[Answer with tick citations<br/>verified vs stated reasons]
  VF -- unsupported --> W
```

- Retrieval is structured (filter by agent, tick window, entity id), not
  embedding search, so it is deterministic and complete.
- Reason attribution (n >= 10 samples per condition, cached):
  1. Baseline: re-run the unchanged prompt to measure how often the choice
     varies by chance (a 2-of-3 "flip" happens 22% of the time at a 30%
     baseline flip rate, so 3 samples are not enough).
  2. Necessity: remove each cited item; it is "necessary" if the choice rate
     drops clearly below baseline.
  3. Sufficiency: keep only the cited items; if the choice holds, the stated
     reasons are enough. Also remove all cited items jointly, which catches
     two reasons that would each suffice alone (single removal misses them).
  4. Caveat: removal can make a prompt incoherent; the ablated prompts are
     regenerated from the structured state, not by deleting text.
  Reasons are then labeled "necessary", "jointly sufficient" or "stated, not
  decisive".
- Counterfactuals ("would you have gone left if the sign were lit?") are real
  re-runs of the recorded decision with that change, reported as a distribution.
- Verification: every sentence must cite a record or event; a cheap judge model
  checks that each cited record supports the sentence; unsupported sentences
  are dropped. The UI shows a grounding badge.

Example answer: "I went to the side exit at 78 s because I saw smoke in the
lobby [t=312, p3] and the side exit sign was lit [p7]. Removing the smoke from
my view changes my choice in 3 of 3 re-runs; removing the sign changes it in 1
of 3. Earlier, a man told me the main door was locked [m_88 from a_09]."

## What this gives us to measure (and show)

- Calibration against the literature: pre-movement time distribution, familiar
  exit share, freeze and hide rates per scenario (research/EVAC_PSYCHOLOGY.md).
- Explanation faithfulness rate: share of stated reasons that are necessary or
  jointly sufficient under ablation, per model and persona. Established method
  (see rule 3), cheap, and a direct answer to "how do you know the agents are
  not just making things up".
- Influence graph: who changed whose beliefs and emotions (rumor spread tree,
  contagion paths), straight from `source` and `emotion_sources` fields.
- Agreement between computed emotion and self-reported feeling.

## Cost and latency

Per decision: one LLM call (deliberation); everything else is code. A repair
or fallback is rare and logged. Interviews: 1 to 2 calls, plus ablation re-runs
only for the clicked occupant (about 3 x number of cited items, cached). With
gemini-2.5-flash-lite this stays within research/LLM_LATENCY.md budgets.

## Corrections from the source check (research/COGNITION_SOURCES.md)

These override the stage table above where they conflict:
- Perception: arousal reallocates attention (threat, exit and on-route cues
  kept, peripheral cues dropped) rather than shrinking it, and only under
  threat. Evacuation evidence for this is indirect.
- Beliefs: source credibility weights are persona-dependent (expertise,
  trustworthiness, protection responsibility per Lindell and Perry); there is
  no published fixed ranking. A warden in person can outrank a PA.
- Appraisal: social proof is a PADM social cue input, not an appraisal
  dimension; "responsibility" here means protection responsibility for
  dependants, not blame; frustration is Scherer goal obstruction, and anger is
  never labeled without someone to blame.
- Contagion: ASCRIBE was fitted to one incident (Dam Square, 2010); use only its
  absorption term (eta = 0). "Fewer cues lower coping" is our modeling
  assumption, not a published effect.
- Milling: for building fires cite Kuligowski and Mileti 2009 and NIST TN 1664
  (information seeking alone did not drive delay).
- The 34% freeze rate comes from earthquake footage, not an intruder scenario.
- Risk to test: emotion text in the prompt can push choices away from human
  patterns (Mozikov et al. 2024); ablate it. Closest prior work: Durupinar,
  arXiv 2607.25140 (2026), LLM appraisal with emergent contagion whose dynamics
  depend on the model backend, which supports computing contagion in code.

Formulas (variables in [0,1]; directions from appraisal theory, product form
from EMA, exact combinations ours and to be calibrated): H = R*S*(1-C);
fear = H*U; anxiety = R*4S(1-S)*(1-C); hope = R*S*C; relief = max(0, H_prev - H);
frustration = R*B; decay e = b + (e - b)exp(-dt/tau) with tau 10-30 s;
contagion q += gamma_R*dt*(q* - q) (ASCRIBE, eta = 0). Full derivations,
parameters and citations: research/COGNITION_SOURCES.md.

## Sources

research/COGNITION_SOURCES.md (this design), research/EVAC_PSYCHOLOGY.md and
research/SCENARIOS_AND_MODELS.md.
