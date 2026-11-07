# Live mode: interactive what-if with LLM occupants (design)

Today every run is computed offline, recorded (decisions.jsonl) and replayed in the
viewer. Live mode lets a user add events and people while watching, without a new engine.

## 1. Branch and stream (not a snapshot engine)
A session holds (scene, scenario, seed, decisions.jsonl). An edit at sim time t creates a
branch: the sim re-runs ticks 0..t from the recorded decisions (no LLM calls; seconds even
for Tepper), applies the edit at t, then continues live. Deterministic, no state
serialization. Every branch writes its own replay.json + decisions.jsonl, so a replay at
the end always exists, is exact, and works with compare and interviews.

## 2. Never block the clock on the LLM
Decisions are requested asynchronously. The agent keeps executing its current intent (or
holds at a door/landing) and the answer applies at the first tick after it returns, within
a deadline of k = 4 ticks (2 s). Past the deadline the persona fallback applies and is
logged as `fallback_deadline`; late answers are discarded. Live is best effort; the
recorded log makes the replay exact.

## 3. Throughput levers (see research/LLM_LATENCY.md)
- Async HTTP, connection reuse, a semaphore starting at 24 in flight with back-off on 429
  (we saw 429s at 32 on this key).
- Burst batching: when more than 8 agents decide in one tick (alarm onset), 8-10 agents per
  call with an array output (4-7x cheaper, ~1.5 s).
- Prefetch: request the next decision when an agent is within ~2 s of a door, fork or
  landing, so it is ready on arrival.
- Static cached prompt prefix; tiny JSON output (max_tokens ~120); reasoning off.
- Deduplicate identical (persona, cues, options) states within a tick.
- Adaptive time warp: target 1x; if pending decisions exceed the budget the sim slows
  (shown in the UI) instead of freezing.

## 4. Why a local server (and not the browser calling the LLM directly)
The OpenRouter key must never ship to a browser (the repo is public), and decisions need
the sim state, the validator, the decision log and replay, which live in Python. So a small
local process (`python -m sim.serve`, and `python -m cognition.serve` for interviews) sits
between the viewer and the API. It is local, starts with one command, and costs nothing
when idle.

## 5. Streaming to the viewer
`python -m sim.serve` (FastAPI + WebSocket): the sim emits per-tick frames as binary
(Float32 x, z per agent, Uint8 state, Uint8 level) plus JSON events at 4-10 Hz. The viewer
buffers ~1 s and interpolates positions every animation frame (60 fps). Viewer -> server
messages: start, pause, speed, edit (validated by scenario.schema.validate), end.

## 6. Editing UI (viewer)
- Click and pick: event palette (fire, smoke, blocked exit/door/stair, rumor, PA
  announcement, open exit), then click a location.
- Click and type: click a spot, type free text ("fire spreading from here", "rumor: the east
  exit is locked"); the scenario LLM tool turns it into a validated event.
- Chat: full sentences through scenario/chat.py (resolve_location, ask_user, submit).
- People: persona palette (staff, regular, visitor, older adult, mobility impaired, parent
  with child, skeptic, committed task, fruit fly) plus trait sliders (walking speed, exit
  familiarity, sign following, social threshold / risk tolerance, Big Five O C E A N);
  click to place one, drag a box to spread N. Before start they join the scenario
  occupants; during a run they spawn at the next tick.

## 7. People that look and act like people
- Behaviour: groups (families and coworkers move together and wait for each other: Sime's
  affiliation model), parents fetch children, staff sweep their floor, speech bubbles from
  the LLM `say` field, emotion visible as a colour ring.
- Rendering: instanced low-poly humanoids (one draw call), heading and walk bob, persona
  cues (staff vest colour, wheelchair, child scale), label LOD, replay parsing in a Web
  Worker, binary live frames, per-panel frustum culling.

## Ownership
sim/ (branch, live loop, serve, groups, spawns): sim owner. cognition/ (async decide,
batching, prefetch, traits in prompts): cognition owner. scenario/ (parse_event for click
and type): scenario owner. blueprint/viewer/src (people rendering, live UI, editing UI):
viewer owner. insights/ (RSET breakdown, influence graph, emotion timelines): Andrew's agent.
