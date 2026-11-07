# Model casting run: hall scan, 36 occupants, one seed (2026-11-07)

Question: does giving different persona groups different LLMs, cast from the
tendencies we measured (SCENARIOS_AND_MODELS.md 3.3), change a fire-alarm
evacuation compared to one model for everyone?

Casting is inspired by documented, version-dependent differences between models
(Akata et al. 2025; Fontana et al. 2025; Jia et al. 2024; see 3.2) and by our own
304-call measurement. It is not a claim that any model has a fixed trait.

## Setup

- Scene `out/hall/scene.json`, alarm at t=0, 300 s, seed 42, `python -m loop run --rounds 0 --decide cognition`.
- Same persona groups and seed in both runs, so spawn points, persona traits and
  pre-movement draws match; only the model differs.
  - `loop/examples/hall_cast.json` (cast): 2 staff on qwen/qwen3.5-flash-02-23, 1 staff on
    deepseek/deepseek-v4-flash (kept to one agent: 11.5 s tail calls in 3.3), 14 regulars on
    meta-llama/llama-4-scout, 8 visitors on anthropic/claude-haiku-5.5, 3 older adults on
    mistralai/mistral-small-3.2-24b-instruct, 2 skeptics on openai/gpt-oss-120b, 6 regulars on
    google/gemini-2.5-flash-lite.
  - `loop/examples/hall_cast_control.json` (control): the same groups, all on gemini-2.5-flash-lite.
- Also listed: the earlier single-model demo baseline (`out/loop/demo_hall/r0_baseline`),
  which used a random persona mix (23 regular, 9 visitor, 1 staff, 1 older adult, 2 skeptic),
  so it is not persona-matched.
- Model ids checked against OpenRouter /api/v1/models. Decision records carry each
  agent's model, so the per-model rows below come straight from decisions.jsonl.

Request shapes: cognition/llm.py sends `reasoning: {enabled: false}` and
`response_format: json_object` to every model. gpt-oss-120b rejects that (400,
"Reasoning is mandatory") and llama-4-scout has no endpoint that accepts it (404).
For this run a launcher outside the repo overrode the request per model (gpt-oss:
`reasoning: {effort: low}`, max_tokens 400; llama-4-scout: neither field). Without
that change both models fall back to rules on every call. cognition/llm.py needs the
same per-model shapes before anyone reruns this live.

## Results

| | cast | control (same groups, flash-lite) | earlier demo baseline (flash-lite, other mix) |
|---|---|---|---|
| evacuated | 33/36 | 34/36 | 34/36 |
| stragglers | older adult 1, skeptic 2 | skeptic 2 | skeptic 2 |
| t90 s | 267.5 | 92.0 | 115.5 |
| pre-movement median s (all) | 33.0 | 31.0 | 25.0 |
| misled by rumor | 0 | 0 | 0 |
| decisions | 158 | 123 | 129 |
| cost $ | 0.046 | 0.027 | 0.027 |

Pre-movement median by persona (s), cast vs control: staff 3.0 vs 3.0, visitor 6.5 vs
6.5, regular 53.0 vs 35.0, older adult 53.8 vs 52.5, skeptic never moved in either.

Per model, cast run:

| model (persona) | agents | decisions | evacuate/follow | seek info | continue task | ok/repaired/fallback | latency p50/max ms | cost $ |
|---|---|---|---|---|---|---|---|---|
| qwen3.5-flash (staff) | 2 | 3 | 100% | 0% | 0% | 3/0/0 | 1918/2715 | 0.0006 |
| deepseek-v4-flash (staff) | 1 | 2 | 100% | 0% | 0% | 2/0/0 | 2757/2757 | 0.0004 |
| llama-4-scout (regular) | 14 | 55 | 25% | 22% | 53% | 40/2/13 | 2477/5981 | 0.0211 |
| gemini-2.5-flash-lite (regular) | 6 | 28 | 21% | 25% | 54% | 26/2/0 | 882/1578 | 0.0050 |
| claude-haiku-5.5 (visitor) | 8 | 18 | 44% | 33% | 22% | 17/1/0 | 1315/2759 | 0.0078 |
| mistral-small-3.2 (older adult) | 3 | 24 | 8% | 21% | 71% | 20/3/1 | 1560/5526 | 0.0059 |
| gpt-oss-120b (skeptic) | 2 | 28 | 0% | 0% | 100% | 27/1/0 | 2529/6728 | 0.0055 |

Control run (all flash-lite): 123 decisions, 29% evacuate/follow, 12% seek info,
59% continue task, 108/13/2 ok/repaired/fallback, latency p50/max 907/2091 ms.

## Interpretation (one seed: any difference below may be noise)

- The outcome barely moved: 33 vs 34 of 36 out, and the same two skeptics stay in both
  runs. Persona, not model, decided who stayed. This matches the 3.3 measurement
  (persona explains about 3x more than model).
- The large t90 gap (267.5 vs 92 s) hangs on a single person. With 36 occupants, t90
  needs 33 out; in the cast run the 33rd exit came late and one older adult (Mistral)
  never left. One seed cannot separate that from chance.
- Where the cast did differ, the direction matches the earlier measurement, but the
  samples are tiny. gpt-oss skeptics chose CONTINUE_TASK in all 28 of their decisions.
  Mistral older adults chose it in 71%. Haiku visitors chose to move in 44% of their
  decisions, against 29% for flash-lite overall. Qwen and DeepSeek staff left at once,
  but so did the flash-lite staff in the control run.
- Regular employees started moving later with llama-4-scout (median 53 s vs 35 s), but
  13 of its 55 decisions (24%) fell back to the rule policy after schema errors (mostly a
  malformed `confidence` field). Part of the "Llama regulars" behavior is therefore the
  fallback rules, not Llama.
- Cost and latency: the cast cost 1.7x the control ($0.046 vs $0.027). Its slowest calls
  were 5.5 to 6.7 s (gpt-oss, Mistral, Llama), against a 2.1 s maximum for flash-lite.
  The sim applies decisions on a fixed tick delay, so slow calls do not change the
  results, but they do slow the run down.

What we can say: casting adds visible variety in actions per group at about 1.7x
the cost. On one seed it did not change how many people got out. Claiming an effect
on evacuation time would need several seeds and persona-matched controls, as here.

## Replays (decisions recorded; replays are free)

- `blueprint/viewer/public/replays/hall_cast` (cast) and `hall_cast_control` (control).
- Side by side: `http://localhost:5190/?scene=/hall/scene.json&glb=/hall/display.glb&replay=/replays/hall_cast_control/replay.json&compare=/replays/hall_cast/replay.json&names=One%20model,Cast&view=side`

Total spend for this run, including the endpoint probes: about $0.075.
