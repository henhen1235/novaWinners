# LLM latency for occupant decisions

Goal: minimize wall-clock time per lockstep decision tick, where the tick waits
for every pending decision. The tick cost is the slowest call in the batch
(the max, not the p50), so tail latency matters more than average latency.

Sources checked 2026-11-07: OpenRouter docs pages provider-routing,
prompt-caching, api-reference/limits, reasoning-tokens, structured-outputs,
latency-and-performance, plus the live `/api/v1/models` and
`/api/v1/models/{id}/endpoints` metadata. Measurements were taken the same
day from macOS over home internet with stdlib urllib (one new TLS connection
per call). Total measurement spend: $0.024.

## Measured numbers

Prompt: about 2.3k to 3.4k token static system prefix (rules plus building
layout), a 40 token per-agent user message, JSON schema output
`{"action": enum(6), "say": string}`, `temperature: 0`, streaming,
`provider: {"sort": "latency"}`. TTFT is time to first content token.

Sequential, 3 calls each (seconds):

| Model (reasoning setting) | Provider hit | TTFT | Total | Cost/call, cold | Cost/call, cached |
|---|---|---|---|---|---|
| google/gemini-2.5-flash-lite (`enabled: false`) | Google AI Studio | 0.48-0.67 | 0.56-0.70 | $0.00029 | $0.00011 (implicit, 1928 tok hit) |
| openai/gpt-oss-120b (`effort: low`, max_tokens 300) | BaseTen | 0.42-0.67 | 0.51-0.78 | $0.00027 | $0.00027 (hit, no discount billed) |
| google/gemini-3.1-flash-lite-preview (`effort: none`) | Google AI Studio | 0.56-0.66 | 0.65-0.76 | $0.00073 | not tested |
| openai/gpt-5-nano (`effort: minimal`, lowest allowed) | OpenAI | 0.82-0.91 | 1.04-1.15 | $0.00013 | $0.00003 (automatic) |
| anthropic/claude-haiku-5.5 (`effort: none`) | Anthropic | 0.61-1.46 | 0.98-1.70 | $0.00036 | $0.00006 (`cache_control`, 3371 tok hit) |
| google/gemini-3-flash-preview (`effort: none`) | Google | 0.91-1.38 | 1.02-1.60 | $0.0015 | not tested |

Concurrent, one call per agent, thread pool (seconds):

| Model | N | Wall (= tick time) | Total p50 | Total max | TTFT p50 | Cost for N |
|---|---|---|---|---|---|---|
| gemini-2.5-flash-lite | 20 | 0.94 / 0.74 (rerun) | 0.57-0.64 | 0.73-0.93 | 0.49-0.53 | $0.0023-0.0039 |
| gemini-2.5-flash-lite | 50 | 0.88 | 0.71 | 0.88 | 0.62 | $0.0076 |
| gemini-2.5-flash-lite `:nitro` | 20 | 0.73 | 0.56 | 0.73 | 0.49 | $0.0041 (1.8x, no gain) |
| gpt-oss-120b, sort latency | 20 | 0.80 | 0.60 | 0.80 | 0.46 | $0.0054 |
| gpt-oss-120b, sort latency | 50 | 0.86 | 0.64 | 0.85 | 0.60 | $0.0134 |
| claude-haiku-5.5, cached | 20 | 1.65 | 1.32 | 1.64 | 1.14 | $0.0012 |

Batched, 20 agents in one call returning a JSON array:

| Model | Total | Output tokens | Cost |
|---|---|---|---|
| gemini-2.5-flash-lite | 1.44 s | 522 | $0.00055 |
| claude-haiku-5.5 | 1.57 s | 396 | $0.00069 |

Other measurements: TCP plus TLS setup to openrouter.ai is 30-50 ms, so
connection reuse saves about 40 ms per call. 50 concurrent calls produced no
429 or 402 errors.

Observations that change the design:
- Output tokens dominate once TTFT is about 0.5 s. A 20-agent batch is about
  2x slower than 20 parallel calls because 400-500 output tokens stream
  serially, but it is 4-7x cheaper.
- Schema `maxLength` was not enforced (Haiku ran to max_tokens and produced
  truncated JSON). Bound free-text fields in the prompt ("at most 8 words")
  and size max_tokens with slack.
- gpt-oss-120b spends hidden reasoning tokens even at `effort: low`; with
  max_tokens 60 one call returned empty content. Give it 300.
- At `temperature: 0`, flash-lite returned identical text for identical
  prompts, and gave the same action for 3 different personalities. Check
  persona diversity before committing to the cheapest model.

## Ranked recommendations

1. Pick a fast small model and turn reasoning off. Impact: 2-3x versus
   reasoning defaults. Haiku 5.5 and Gemini 3 Flash report
   `default_effort: "medium"` in `/models`; send `reasoning: {"effort":
   "none"}` (Gemini 2.5 flash-lite: `{"enabled": false}`). GPT-5 nano/mini
   have `mandatory: true` reasoning and reject `none`, so `minimal` is the
   floor, which is why nano measured about 1.05 s. Default:
   `google/gemini-2.5-flash-lite` (0.56 s single, 0.9 s tick at N=50).
   Alternative with comparable speed: `openai/gpt-oss-120b` with
   `provider.sort: "latency"`.
2. Hide latency in sim time instead of pausing the world. Impact: tick time
   approaches the sim step time instead of the LLM max. Model a fixed reaction
   delay: a decision requested at tick t is applied at tick t+k (k = 1-2 s of
   sim time, a realistic human reaction time). The world keeps stepping; only
   block at tick t+k if that response has not arrived. This stays
   deterministic because the apply tick is fixed by the rule, not by arrival
   time. Same idea for forks: request the decision when the agent is a few
   steps from the fork, using the observation at request time.
3. Keep the prompt short in output, long and static in prefix. Impact: cost
   2.5-7x lower, latency 0-0.3 s lower. One byte-identical system message
   (rules, scene, action list) of at least 1024 tokens, then a short user
   message with persona and event. Gemini 2.5 flash and OpenAI cache
   implicitly at 1024 tokens; Anthropic needs `cache_control` (top-level
   automatic or per block, min 512-4096 tokens by model, 1.25x write, 0.1x
   read, 5 min TTL). Put persona in the user message, not the system message:
   Gemini treats the first system message as the immutable cached part.
   Output a tiny enum plus a bounded `say`; `max_tokens` 48 for non-reasoning
   models.
4. Bound the tail, not the mean. Impact: tick time stops depending on p99
   (Haiku endpoints show p99 TTFT of 4-12 s in the 30 min stats). Per-tick
   deadline of 2.0 s; on deadline, use a deterministic rule-based fallback
   and record it as the decision. Optionally hedge: if a call has not started
   streaming after 1.0 s, send a duplicate and take the first. Use
   `provider.preferred_max_latency: {"p90": 1.5}`, which reorders endpoints
   using a rolling 5 min window without excluding any.
5. Record and replay every decision. Impact: demo replays run at zero LLM
   latency and zero cost, and are byte-identical. Live LLM output is not
   deterministic even at temperature 0, and timeouts make it worse. The
   decision log (tick, agent id, prompt hash, model, result, `source:
   llm|fallback`) is the source of truth. Replay mode reads the log and never
   calls the network. The demo should show a recorded run by default and a
   live run as the option.
6. Deduplicate within a tick. Impact: proportional to duplicate rate. Hash
   (model, messages, params); identical requests in the same tick share one
   call. Bucket the observation (room id, smoke yes/no, crowd density bin) so
   duplicates are more likely. The disk cache by SHA-256 already
   covers repeats across runs.
7. Use cheap per-agent calls for routine decisions, a stronger model for rare
   ones. Impact: quality where it is visible, small cost. Routine (alarm,
   fork, blocked path): flash-lite. Rare, narratively visible (helping an
   injured person, leader speech): `anthropic/claude-haiku-5.5` with
   `cache_control` (1.0-1.6 s, cheapest per cached call at $0.00006).
8. Batch only when cost binds. Impact: 4-7x cheaper per decision, 2x slower
   per tick at N=20. If N is in the hundreds and the budget is tight, batch
   5-10 agents per call so output per call stays near 100-200 tokens.
9. Skip these: `:nitro` (1.8x price on flash-lite, no measured gain; it
   enables priority-tier endpoints), `:floor` and any `flex` endpoint (flex
   tags show 2-14 s p50 latency), hardcoded `provider.order` (disables sticky
   cache routing), HTTP/2 or a new HTTP client (about 40 ms saved per call;
   stdlib threads handled 50 concurrent calls in 0.88 s).

## Limits that can bite at scale

- Paid models have "no platform-level request cap" on OpenRouter. Limits are
  per model and per provider.
- In-flight spending budget: with a prepaid balance below a threshold,
  OpenRouter holds the estimated cost (input tokens plus `max_tokens`) of
  running requests against a fraction of the balance (fraction not
  documented). Exceeding it returns HTTP 402 with `metadata.reason:
  in_flight_budget_exhausted` and a `Retry-After` header. A small
  `max_tokens` keeps the hold small. Treat 402 like a timeout: fall back and
  record.
- Low balance adds latency: OpenRouter runs extra DB checks when the balance
  is in "single digit dollars", which applies to this $7 key. Not measured
  separately.
- The first 1-2 minutes in a new region run on cold OpenRouter caches. Warm
  up with one call before a live demo.

## Budget math

At cached flash-lite pricing ($0.00011 per call with a 2.7k token prefix),
$6 buys about 55k decisions: 200 agents x 5 decisions x 50 runs. Haiku
cached is $0.00006 per call but slower. The prefix size drives cost, so
trimming the layout text from 2.7k to 1.1k tokens roughly halves it while
staying above the 1024-token cache minimum.

## Recommended default config

```json
{
  "model": "google/gemini-2.5-flash-lite",
  "reasoning": {"enabled": false},
  "temperature": 0,
  "max_tokens": 48,
  "stream": false,
  "response_format": {"type": "json_schema", "json_schema": {"name": "decision",
    "strict": true, "schema": {"type": "object", "additionalProperties": false,
    "required": ["action", "say"], "properties": {
      "action": {"type": "string", "enum": ["..."]},
      "say": {"type": "string"}}}}},
  "provider": {"require_parameters": true, "preferred_max_latency": {"p90": 1.5}},
  "session_id": "<run id>"
}
```

- Prompt layout: message 1 `system` = static rules plus scene, byte-identical
  across all agents and ticks of a run, at least 1024 tokens. Message 2
  `user` = persona, current observation, allowed actions for this event.
- Rare decisions: `anthropic/claude-haiku-5.5`, `reasoning: {"effort":
  "none"}`, system content as a block with `cache_control: {"type":
  "ephemeral"}`, `max_tokens` 64.
- Concurrency: one call per agent, semaphore 64.
- Timeout: 2.0 s tick deadline, 1 retry is not worth it inside the deadline.
  Late results are discarded, not applied later.
- Replay: every decision, including fallbacks, goes to a JSONL log keyed by
  (run id, tick, agent id). `--replay log.jsonl` never calls the network.
- `stream: false` in production: with 30-token outputs streaming saves
  under 0.1 s and the sim needs the whole JSON anyway. Streaming was used
  above only to measure TTFT.

## Minimal async client sketch

Stdlib only (urllib plus `asyncio.to_thread`), which is what produced the
numbers above. Not run as-is; the bench script it is derived from was.

```python
import asyncio, hashlib, json, ssl, urllib.request
import certifi

URL = "https://openrouter.ai/api/v1/chat/completions"
CTX = ssl.create_default_context(cafile=certifi.where())
DEADLINE_S = 2.0
SEM = asyncio.Semaphore(64)


def _post(body: dict, api_key: str) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=CTX, timeout=DEADLINE_S + 1) as r:
        return json.load(r)


def prompt_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


async def decide(body: dict, api_key: str, fallback: dict, allowed: set) -> tuple[dict, str]:
    async with SEM:
        try:
            resp = await asyncio.wait_for(asyncio.to_thread(_post, body, api_key), DEADLINE_S)
            out = json.loads(resp["choices"][0]["message"]["content"])
            if out.get("action") in allowed:
                return out, "llm"
        except Exception as e:  # timeout, 402/429/5xx, bad JSON: all fall back
            print(f"decision fallback: {type(e).__name__}: {e}")
        return fallback, "fallback"


async def run_tick(tick: int, requests: list, api_key: str, log, replay: dict | None):
    """requests: [(agent_id, body, fallback, allowed)]. Returns {agent_id: decision}."""
    if replay is not None:
        return {aid: replay[(tick, aid)] for aid, *_ in requests}
    by_hash = {}  # identical prompts in one tick share a call
    for aid, body, fb, allowed in requests:
        by_hash.setdefault(prompt_hash(body), (body, fb, allowed, []))[3].append(aid)
    keys = list(by_hash)
    results = await asyncio.gather(*(decide(*by_hash[k][:3], api_key) for k in keys))
    decisions = {}
    for k, (out, source) in zip(keys, results):
        for aid in by_hash[k][3]:
            decisions[aid] = out
            log.write(json.dumps({"tick": tick, "agent": aid, "hash": k,
                                  "source": source, "decision": out}) + "\n")
    return decisions
```

`asyncio.wait_for` does not stop the worker thread; the late response is
dropped, which is the intended "discard late results" policy. Note the
fallback must depend only on sim state so the live run and its log agree.
