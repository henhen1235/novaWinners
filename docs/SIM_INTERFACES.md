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
`at` is exactly one of {"room": id}, {"exit": id}, {"door": id}, {"point": [x, z], "level": id}.
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
 "frames": [{"t": 0.0, "a": [["a_01", 3.2, 4.1, "moving|deciding|waiting|queued|exited|helping|hiding"]]}],
 "events": [{"t": 12.5, "kind": "decision|said|heard|exited|blocked|fire_spread|rumor",
             "agent": "a_01", "ref": "d_000012", "text": "..."}],
 "metrics": {"evacuated": 0, "total": 0, "t_last_exit_s": 0, "exit_usage": {"e_001": 0},
             "pre_movement_s": {"median": 0, "p90": 0}, "per_persona": {}}}
```
Frames every 0.5 s (positions rounded to 1 cm). The decision log is written next to it as
decisions.jsonl (one DecisionRecord per line, docs/AGENT_COGNITION.md).

## Viewer

`blueprint/viewer` loads `/?glb=...&scene=...&replay=...`; plays frames with a time
slider, colors agents by state or persona, shows events, and opens an interview panel
for a clicked agent that calls the local interview API (`python -m cognition.serve`,
POST /interview {run, agent_id, question} -> {answer, citations, verified}).
