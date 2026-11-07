# Rehearsal

Rehearsal turns an iPhone LiDAR scan or a building's floor plans into a validated 3D
model and runs an evacuation drill in it with LLM-driven occupants, so facility
managers, schools and offices can see where people hesitate, who gets left behind,
and which fix helps, before a real alarm.

An adversary agent attacks the building (locks an exit, starts a fire, plants a
rumor), an optimizer agent proposes fixes from a fixed menu, and the simulator
measures before and after. The goal is a realistic picture of what would happen
and a set of tested alternatives, not a run where everyone survives: stragglers are
findings. It is a planning tool; it does not certify a building.

## Pipeline

```
iPhone RoomScanner app (RoomPlan or LiDAR Mesh mode)  --+
CAD/PDF floor plans (blueprint pipeline, ingest_plans)  +--> scene.json + display.glb
CMU Tepper ESIM sheets (tools/tepper_to_scene.py)     --+        (contract/, validated)
        |
        v
analysis/   single points of failure, pinch points, smoke clock, occupant load
scenario/   plain-language scenario chatbot (LLM tool calling) -> scenario.json
sim/        navmesh crowd sim, multi-floor stairs, decide() hook, replay.json
cognition/  LLM occupants: computed perception/beliefs/emotion, PADM decision with
            evidence ids, validator, decision log; interview server with verified reasons
loop/       adversary vs optimizer: baseline -> attack -> fixes -> re-run, report.html
flyagent/   (extra) fruit-fly connectome occupant + scrambled-wiring control
blueprint/viewer  Three.js viewer: replay, side-by-side/stacked/exploded floors,
            cutaway walls, interview panel, analysis overlay, compare runs
```

## Quickstart

Needs Python 3.11 and Node 20+.

```bash
scripts/demo/setup.sh     # .venv, requirements, npm ci, builds demo scenes, analyses, replays
scripts/demo/start.sh     # interview API on :8771, viewer on :5173; prints and opens demo URLs
scripts/demo/stop.sh
```

`setup.sh` makes no LLM calls. The demo walkthrough, URLs and fallback plan are in
[docs/DEMO.md](docs/DEMO.md). LLM features read `OPENROUTER_API_KEY` from the
environment or `.env`.

Single steps:

```bash
.venv/bin/python -m ingest_lidar scan.ply -o out/hall            # mesh, RoomPlan JSON, OBJ/GLB/USDZ
.venv/bin/python -m ingest_plans <floor PDFs or folder> -o out/b  # CAD-exported floor-plan PDFs, multi-floor
.venv/bin/python -m contract.validate_scene out/hall/scene.json
.venv/bin/python -m analysis out/hall/scene.json -o out/hall/analysis.json
.venv/bin/python -m sim run --scenario sim/examples/tiny_office_fire.json -o out/sim/x --seed 42 [--decide cognition] [--no-llm]
.venv/bin/python -m loop run --scene S --scenario SC -o out/loop/x [--decide cognition] [--no-llm]
.venv/bin/python -m cognition.serve --runs out/sim --port 8771     # interview API for the viewer
.venv/bin/python -m pytest -q tests
```

## Results so far

All single runs with seed 42 unless noted.

- LiDAR ingest, 30 synthetic scans: up axis within 2 degrees on 100 percent, walkable IoU
  0.958 / 0.914, door recall 0.996 / precision 0.982, room count within +-1 on 100 percent,
  median wall error 6 mm, about 2 to 8 s per scan. A 1.67M-triangle real mesh ingests in 4.8 s.
- Real scan of a 14 x 14 m hall (iPhone Mesh mode): valid scene, curved walls traced, 19
  people removed from the scan by OWLv2 plus LiDAR, 4 real exit signs detected.
- Tepper (CMU), floors 1 to 5 from floor plans: 576 rooms, 915 doors (28 inferred and
  flagged), 29 exits, 12 stair links, validator 0 errors.
- Tepper fire, 170 rule-based occupants: without an assisted-evacuation plan 166/170 get
  out and 3 wheelchair users wait at stair refuges on floor 4; with the plan 169/170, all 3
  carried out by staff.
- Hall, adversary vs optimizer, 36 LLM occupants (gemini-2.5-flash-lite): baseline 34/36
  out; adversary locked exit e_001 (used by 23, 38.8 percent of exit width): 15/36; optimizer
  unlocked it, posted 2 wardens, added signage: 33/36. 400 decisions cost $0.086, about 0.94 s
  per decision; replays are free.
- LLM pre-movement median 63.8 s against a research target of about 60 s.
- Interviews: removing the fire percept flips the cited choice in 7 of 7 re-runs; removing a
  stated belief flips it in 0 of 7. The answer reports which reasons were decisive.

## Where AI is used

- Occupants: an LLM makes the protective-action decision (PADM questions) at discrete
  decision points, citing evidence ids that code checks; invalid output is repaired once,
  then falls back to a persona rule. Personas and timing come from evacuation research.
- Interviews: answers come only from the occupant's decision log, and each stated reason
  is tested by re-running the decision without it.
- Scenario chatbot: plain language to a validated scenario.json by tool calling.
- Adversary and optimizer: the adversary picks failures with a number-backed reason; the
  optimizer picks fixes from a fixed menu (warden, signage, PA, unlock, familiar exit,
  assisted-evacuation plan, widen door), and each patch is validated before it runs.
- Capture: LLM room labeling, occupancy seeding, OWLv2 exit-sign detection and people
  removal on scan keyframes, Claude-assisted reading of the Tepper plan legend.

Where it is deliberately not used: geometry. Walls, rooms, doors, the walkable navmesh,
routes, walking, queuing and door flow are computed and validated in code. LLMs never
output coordinates.

## Modelling choices and limits

- No "panic" behavior: the evacuation literature treats mass panic as rare; delays come
  from noticing, interpreting and preparing.
- Fire and smoke are events that block rooms and doors on a schedule; there is no fire or
  smoke physics. The smoke clock is a closed-form estimate.
- Elevators are recalled in fires and not used; occupants may walk to an elevator lobby
  and turn back.
- Movement follows standard door-flow and walking-speed limits; occupant decisions are not
  validated against real evacuations.

## Repo map

| Path | What |
|---|---|
| `contract/` | Frozen scene format (models, JSON schema, validator), fixtures |
| `ingest_lidar/` | Scan (mesh or RoomPlan) to scene.json, display.glb, debug images |
| `ingest_plans/` | Floor-plan PDFs to a validated multi-floor scene |
| `blueprint/` | Blueprint pipeline (vector and raster PDFs) and the Three.js viewer (`blueprint/viewer`) |
| `ios/RoomScanner` | iPhone capture app (RoomPlan and LiDAR Mesh modes) |
| `analysis/` | Single points of failure, pinch points, smoke clock, occupant load |
| `scenario/` | Scenario schema, resolver and chatbot |
| `sim/` | Crowd simulator, stairs, refuges, assisted evacuation, replay output |
| `cognition/` | LLM occupant decisions, validator, decision log, interview server |
| `loop/` | Adversary vs optimizer rounds, scoreboard, report.html |
| `flyagent/` | Fruit-fly connectome occupant (extra) |
| `tools/` | Synthetic scans, benchmark scoring, plan rendering, exit tagging, Tepper converter |
| `samples/`, `fixtures/` | Sample buildings (Tepper model, RoomPlan samples) and test scenes |
| `scripts/demo/` | Demo setup, start and stop |
| `design/` | Design tokens and visual system |
| `docs/`, `research/` | Interfaces, cognition design, demo script, research notes |

## Docs

- [docs/DEMO.md](docs/DEMO.md): 3-minute demo, URLs, fallback
- [docs/SIM_INTERFACES.md](docs/SIM_INTERFACES.md): scenario.json, decide() hook, replay.json, interventions
- [docs/AGENT_COGNITION.md](docs/AGENT_COGNITION.md): occupant cognition, decision records, interviews
- [docs/LIVE_MODE.md](docs/LIVE_MODE.md): interactive what-if design
- [docs/pitch/](docs/pitch/): pitch deck and submission writeup
- [research/](research/): evacuation psychology, scenarios and models, competitive analysis, LLM latency
- `PLAN.md`, `DECISIONS.md`, `STATUS.md`, `DATA.md`, `SCAN_GUIDE.md`, `blueprint/AUDIT.md`
