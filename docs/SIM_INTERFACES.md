# Sim interfaces (shared by the sim, cognition, scenario and viewer work)

All coordinates: scene frame from scene.json (meters, Y up, 2D [x, z], level ids).
All files JSON (or JSONL); deterministic given (scene, scenario, seed, decision log).

## scenario.json (input; produced by hand or by `python -m scenario` chat)

```json
{"schema_version": "0.1", "scene": "out/hall/scene.json", "seed": 42, "duration_s": 300,
 "events": [{"id": "ev1", "type": "fire|smoke|alarm_only|active_intruder|blocked_exit|rumor",
             "t_start_s": 0, "at": {"room": "r_003"} , "params": {}}],
 "occupants": [{"where": {"room": "r_002"}, "count": 10,
                "personas": {"regular": 0.6, "visitor": 0.3, "staff": 0.1},
                "models": {"google/gemini-2.5-flash-lite": 1.0}}],
 "assumptions": []}
```
`at` is exactly one of {"room": id}, {"exit": id}, {"door": id}, {"point": [x, z], "level": id}. The sim also accepts {"stair": id}.
Event semantics (minimum): fire/smoke make the event room and its doors high-cost then
impassable after `params.block_after_s` (default 60); blocked_exit makes that exit
impassable from t_start_s; rumor injects a message `params.text` claiming
`params.claim` (e.g. {"exit_blocked": "e_002"}) to occupants within `params.radius_m`
(default 8); alarm_only starts the cue; active_intruder is a scripted mover
(`params.path` of points, speed) that occupants avoid.

## Sim tick and the decide() hook

Lockstep clock, dt = 0.5 s. The sim moves occupants on the navmesh (A* over
triangles + string pulling, speed by persona, simple queuing at doors: max flow
1.3 persons/s per meter of door width). At decision points the sim calls

```python
decide(view: dict) -> dict   # pure function of `view`; may call an LLM; must return quickly
```
`view` = {"agent": {id, persona, model, pos, room, known_exits, traits}, "t_s", "tick",
"trigger": {"kind", "ref"}, "percepts": [...], "beliefs": [...], "options": [...]} and the
return value = {"choice": option_id, "say": str|None, "record": DecisionRecord} where
DecisionRecord follows docs/AGENT_COGNITION.md. A default rule-based decide() lives in
the sim so it runs with no LLM (`--no-llm`). Decisions apply at tick + k (k = 2, a 1 s
reaction delay) so LLM latency never changes results; LLM outputs are recorded and replayed.

## replay.json (output; consumed by the viewer)

```json
{"meta": {"scene": "...", "scenario": "...", "seed": 42, "dt": 0.5, "duration_s": 300,
          "llm_calls": 0, "cost_usd": 0.0},
 "agents": [{"id": "a_01", "persona": "visitor", "model": "...", "color": "#..."}],
 "frames": [{"t": 0.0, "a": [["a_01", 3.2, 4.1, "moving|deciding|waiting|queued|exited|helping|hiding|waiting_at_refuge|carried", "L1"]]}],
 "events": [{"t": 12.5, "kind": "decision|said|heard|exited|blocked|fire_spread|rumor|alarm|opened|waiting_at_refuge|helping",
             "agent": "a_01", "ref": "d_000012", "text": "..."}],
 "metrics": {"evacuated": 0, "total": 0, "t_last_exit_s": 0, "exit_usage": {"e_001": 0},
             "pre_movement_s": {"median": 0, "p90": 0}, "per_persona": {}}}
```
Frames every 0.5 s (positions rounded to 1 cm). Each frame entry is
[id, x, z, state, level]; the level id is the 5th element (appended, so readers of the
4-element form keep working). The sim stops writing frames once everyone has exited
(meta.t_end_s). The decision log is written next to it as
decisions.jsonl (one DecisionRecord per line, docs/AGENT_COGNITION.md).

## What sim/ implements (sim/, read this before plugging in)

CLI: `python -m sim run --scenario S [--scene scene.json] -o OUT --seed N [--decide cognition|pkg.mod:func] [--no-llm]`,
`python -m sim baseline ...` (no delay, omniscient shortest path), `python -m sim compare OUT1 OUT2 ...`.
OUT gets replay.json, decisions.jsonl, metrics.json. `--no-llm` sets NOVA_NO_LLM=1 and the run
directory is in NOVA_RUN_DIR. If the decider module has `decide_batch(views) -> results`, the sim
calls it once per tick with every agent deciding that tick (parallel LLM calls); otherwise `decide`.
A result whose choice is not an option id, or an exception, falls back to the rule policy and the
record's validation.status is "fallback".

Levels: the navmesh, walkable, rooms, doors and exits are per level (triangle_level); stairs come
from scene.links (type stairs, cost_multiplier, bidirectional) or scene.stairs. Stair speed is a
persona trait (0.6 m/s down, 0.4 up; older adults 0.3 down), stair flow 1.1 persons/s per meter of
stair width. Elevators are never used. People who cannot use stairs (mobility_impaired) are offered
GO_TO_REFUGE and wait at the landing (state waiting_at_refuge) until a staff agent chooses HELP;
the helper carries them (0.6 m/s level, 0.3 m/s stairs, state helping / carried). Without an
assisted-evacuation plan (the default) HELP is offered only to staff who can see someone waiting
at a refuge, and the rule policy accepts it for about 30 percent of such pairs (NCSTAR 1-7: 30
percent helped someone); stragglers at refuges are a finding. With the plan
(`interventions: [{"type": "assisted_evacuation_plan"}]` or `params.assisted_evacuation: true`)
the nearest free staff member for each person who cannot use stairs (planar meters plus 30 m per
floor apart, path reachable) is radioed, offered HELP with `assigned: true` and the rule policy
always takes it; at most one person per staff member. Events help_assigned, helping (pick-up),
help_released (helper gave up or left). The person entered by is drawn with a
preference for entrances near where they start (weight exp(-route_m / 60)).

The decider owns the choices; the sim only executes walking, queuing and stopping. Decision
triggers (`trigger.kind`): cue_noticed, premove_done, periodic (20 s waiting, 10 s seeking info),
hazard_seen, path_blocked (exit or stair found blocked, route cut), route_changed (hazard costs
changed; the agent keeps walking its old path until the decision applies), rumor_heard, heard
(speech only when it is news for the listener or from staff/PA, at most once per 8 s per listener),
door_reached, level_reached (every landing: the agent stops until the decision applies; keep going
down or leave the stairwell), stair_smoke / stair_crowded (once per stair and floor, agent waits),
help_request (staff, radio; only under an assisted-evacuation plan), picked_up (helper, after picking someone up).

view.agent: id, persona, model, pos [x, z], level, room, known_exits, entry_exit, target_exit,
target_stair, carrying (id or null), traits (speed_mps, premove_s, notice_s, sign_follow_p,
social_threshold, stair_down_mps, stair_up_mps, uses_stairs, t_since_cue_s, activity, state, pace).
premove_s is a draw from the persona's lognormal (research/EVAC_PSYCHOLOGY.md) with its long tail
and no cap. The rule policy moves at premove_s, earlier on a strong cue; skeptics ignore the alarm
alone and move on a second cue (staff or PA instruction, smoke, two or more people seen leaving).

view.percepts kinds: alarm, fire / smoke (where, level, dist_m), people_moving (count, toward),
person (ref, persona, doing, state, toward, dist_m; up to 8 nearest in sight), message (from, text,
urgent, official), exit_sign (ref, dist_m), stair (ref, direction, leads_to_level, on_stairs,
waiting, smoke, blocked, dist_m), needs_help (staff only: ref, level).

view.options (ids o1..): EVACUATE {exit, label, route_m, queue_s, eta_s, through_hazard, stair};
TAKE_STAIRS {stair, direction, route_m, queue_s, eta_s, leads_to_level, blocked, smoke} (floors
with no exit); GO_TO_REFUGE {stair, route_m, eta_s}; HELP {target, level, route_m, eta_s} (staff);
FOLLOW {target, exit, route_m, eta_s}; SEEK_INFO; CONTINUE_TASK. route_m is meters, eta_s uses this
agent's own brisk speed plus queue and stair time. Movement options carry "paces":
["calm", "brisk", "hurry"]; the result may set "pace" at top level or in record.decision.pace
(speed x0.85 / 1.0 / 1.25, capped by persona; hurrying people refused at a door slow its flow
slightly). Missing or invalid pace means brisk.

Scenario extras the sim reads: events open_exit {exit} and pa_announcement {params.text,
params.claim} (official, urgent, reaches everyone); blocked_exit / fire / smoke can target a
stair ({"stair": id} or a stairwell room); occupants[].familiar_exit sets the exit they came in by;
`interventions: [{"type": "signage", "exit": id, "p": 0.8}]` makes each occupant know that exit
with probability p; with `"room": id` it only reaches people who start in that room or later
decide while standing in it (once each). A claim `{"exit_open": id}` (string or list) from an
official source (pa_announcement or staff speech) overrides a hearsay blocked belief; own sight
still wins over both. A rumor is a social cue, not an alarm: it never starts anyone's cue, and
speech is urgent only from staff or from someone who saw the hazard. If a scenario has no
alarm_only, fire, smoke or pa_announcement event, the alarm cue starts at t = 0. Wardens are occupants with persona staff. For programmatic drivers (loop/),
`Sim(scene, scenario, seed, decider, hooks=[fn])` calls fn(sim) at the start of every tick.

Event kinds added by the sim: alarm, opened, waiting_at_refuge, help_assigned, helping,
help_released. metrics.json adds exit_time_s, stragglers, misled_by_rumor, decisions, alarm_t_s, t_end_s.

## Live mode (sim/live.py, sim/serve.py; design in docs/LIVE_MODE.md)

Branch: `sim.live.branch(scene, scenario, seed, records, t, events, occupants, decider, history=...)`
re-runs ticks 0..t with the decisions in `records` (decisions.jsonl; no decider calls), re-applies
earlier live edits at their ticks, adds the new events/occupants for the next tick and returns a
Sim to continue (`sim.run()` or `sim.advance()` per tick). With no edit it reproduces the original
replay.json exactly. `Sim.add_events` (scenario-schema events; past t_start_s means the next tick)
and `Sim.add_occupants` (scenario-schema occupant groups, optional `"traits": {"speed_mps": 1.1,
"premove_s": 30, ...}` overriding the draws; they notice the alarm after their own notice delay from
the moment they appear; event "spawned") work on any Sim between ticks.

Async decisions: `sim.async_dec = AsyncDecisions(decide)` sends each decide(view) to a thread pool
(24 workers); an answer applies at the first tick after it returns, within 4 ticks (2 s). Otherwise
the rule policy applies and the record's validation.status is "fallback_deadline"; late answers
are dropped. Records keep the request tick and the real apply_tick, so a branch replays them exactly.

Server: `python -m sim.serve --port 8790` (needs the `websockets` package for uvicorn's WebSocket
support). One session per WebSocket at `ws://127.0.0.1:8790/ws`; `GET /health`.

Client to server (JSON):
- `{"type": "start", "scene": path|object, "scenario": path|object, "seed": 42, "decide": "policy"|"cognition", "no_llm": true, "speed": 1.0}`
- `{"type": "pause"}`, `{"type": "resume"}`, `{"type": "speed", "value": 2.0}`
- `{"type": "edit", "events": [...], "occupants": [...]}`: validated with scenario.schema.validate
  against the scenario plus earlier edits; applied at the next tick.
- `{"type": "branch", "t": 30.0, "events": [...], "occupants": [...]}`: branch from this session's
  decisions at an earlier t (the current branch is written out first).
- `{"type": "end"}`: stop and write the run directory.

Server to client: JSON `meta` (session, branch, dt, duration_s, levels, states, agents with index),
`agents` (newly spawned, appended in index order), `events` (replay event objects, new since the
last message), `status` (about 1 Hz: t, tick, speed, warp, paused, ticks_per_s, decisions, evacuated,
total, pending, async stats incl. fallback_rate and latency), `edit_ok`, `error` (for, errors),
`finished` (run reached its end; the session pauses), `ended` (run_dir). Binary frames, at most 5 Hz,
little-endian: float32 t, uint32 n, float32[2n] x,z per agent in index order, uint8[n] state code
(index into meta.states: waiting, moving, deciding, queued, exited, helping, hiding,
waiting_at_refuge, carried, at_elevator_lobby), uint8[n] level index (into meta.levels).
Each branch is written to `out/live/<session>/branch_<k>/` (replay.json, decisions.jsonl,
metrics.json, edits.json; NOVA_LIVE_RUNS overrides out/live), so the viewer replay, compare and
interviews work on it.

## Viewer

`blueprint/viewer` loads `/?glb=...&scene=...&replay=...`; plays frames with a time
slider, colors agents by state or persona, shows events, and opens an interview panel
for a clicked agent that calls the local interview API (`python -m cognition.serve`,
POST /interview {run, agent_id, question} -> {answer, citations, verified}).

## Interventions (loop/ optimizer patches)

The optimizer (`loop/optimizer.py`) may only pick from this menu; `loop/patch.py` checks each
intervention, applies it as a new (scene, scenario) pair (inputs never mutated), then validates
the result: `scenario.schema.validate` after checking the sim extras above, and
`contract.validate_scene` must report no new errors. Rejected patches return the error strings
to the model for repair. Each round writes `out/loop/<name>/rN/interventions.json`:

```json
{"round": 1, "interventions": [
  {"id": "fix1_1", "kind": "unlock_exit", "args": {"exit": "exit_01"}, "why": "21 of 30 used it ..."}]}
```

| kind | args | patch | limits |
|---|---|---|---|
| add_warden | room, count | occupants += {where: {room}, count, personas: {staff: 1.0}} | count 1..2 |
| add_signage | room, exit | interventions += {type: signage, exit, p: 0.8, room} | occupants of the room and passers-by |
| pa_announcement | t_s, text, exit_open? | events += pa_announcement at t_s, params.claim {exit_open} | t_s within the run |
| unlock_exit | exit | events += open_exit at (blocked_exit t_start_s + 30 s) | exit must have a blocked_exit with cause locked, chained or obstructed |
| assign_familiar_exit | room, exit | occupants in room get familiar_exit = exit | |
| assisted_evacuation_plan | (none) | interventions += {type: assisted_evacuation_plan} | once; trained staff carry people who cannot use stairs |
| widen_door | door, width_m | scene doors[].width_m (and the exit's width_m) | > current, <= min(1.5x, 2.4 m) |

Adversary injections are plain scenario events with ids `advN_k`: blocked_exit
(params.cause locked|chained|obstructed), fire/smoke (params.block_after_s), rumor
(params.text, params.claim.exit_blocked). At least one exit must stay open, each `why` must cite
a number, at most 3 per round.

Loop runs the sim in-process; the sim itself sets `exit_open:<exit>` from an official
pa_announcement claim, and loop rewrites that message for cognition as percept kind "pa" with
the claim. Scoreboard metrics count only the original
occupants (wardens are excluded): t95_s (time from alarm to 95% out; the run duration when never
reached), evacuated, stragglers, misled_by_rumor, max_queue (most agents queued in one frame),
llm_calls, cost_usd, and per-tick LLM latency (slowest call of each tick's batch).
