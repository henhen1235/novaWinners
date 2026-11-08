# Rehearsal

Point an iPhone at a hallway, or drop in a floor plan, and Rehearsal builds a measured 3D building and fills it with people who decide for themselves. They wait, follow the way they came in, believe a rumor, or head for a stair they cannot use. An adversary then breaks the plan on purpose. An optimizer proposes a fix. The simulator runs it again and keeps the number.

This is a fire drill you can rewind. Facility teams see who is still inside, where the crowd bunches, and which single change is worth making. It is a planning tool. It does not certify a building.

## Nine models, one building

Nobody in the building is stuck on a single model. A full-LLM occupant is assigned **at random** from three models, so one model's habits cannot decide the outcome for the whole floor:

| Model | What it does |
|---|---|
| **gpt-oss-120b** | Full-LLM occupants, and the interview answers when you click a person |
| **Claude Sonnet 5.5** | Full-LLM occupant |
| **ChatGPT Luna 6** | Full-LLM occupant |

Around them, specialist models do the jobs they are fast at:

| Model | What it does |
|---|---|
| **Gemini 2.5 Flash-Lite** | Hybrid occupants: a playbook for the ordinary moment, a real model call when the situation breaks the script. Also Populate, and click-and-type edits on the plan |
| **Gemini 2.5 Flash** | The adversary and the optimizer. One attacks the building. The other picks a fix and the sim measures it |
| **Claude Opus 5.5** | The review after a run: how the building did, the single highest-value change, and a grade |
| **Claude Haiku 5.5** | The scenario chatbot, and room labels on a LiDAR scan |
| **OWLv2** | Vision. Finds exit signs in the scan and removes the people who were standing in the room |
| **Fruit-fly connectome** | An occupant whose choices come from a simulated fly brain. Scrambled wiring is the control. It is in the product because it is a real decide() path, and because it is fun |

Geometry never goes through a model. Walls, doors, the walkable mesh, routes, walking speed, queues, and door flow are computed and checked. Models choose. Code moves people.

## What you can do with it

Open the viewer, pick a building, and either play a recorded drill or start from an empty plan and put people in yourself.

- **Scan or plan in, building out.** RoomPlan, a LiDAR mesh, or a PDF floor plan becomes `scene.json` plus a `display.glb`, in meters, validated before anyone walks.
- **Hundreds of people, each with a mind.** Perception, beliefs, and fear are computed. The protective-action decision cites evidence the code can check. A bad answer is repaired once, then a persona rule takes over so the drill never freezes.
- **Click a person.** Ask why they moved. The answer comes only from their own decision log, and a stated reason is tested by running the decision again without it.
- **Attack, then repair.** The adversary locks the exit people are actually using, or starts the fire where it hurts. The optimizer answers from a fixed menu: warden, signage, a PA message, unlock, a familiar exit, an assisted-evacuation plan, a wider door. Every patch is validated, re-run, and scored.
- **Evaluate.** After the run, Claude Opus 5.5 reads the building and writes the sheet: the account of what happened, the one change that matters most, and a grade.

## Run it

Python 3.11 and Node 20+.

```bash
scripts/demo/setup.sh     # venv, dependencies, demo buildings, analyses, replays. No model calls.
scripts/demo/start.sh     # interview API on :8771, viewer on :5173
scripts/demo/stop.sh
```

Model features read `OPENROUTER_API_KEY` from the environment or from `.env`. The key stays there.

One building, end to end:

```bash
.venv/bin/python -m ingest_lidar scan.ply -o out/hall
.venv/bin/python -m ingest_plans <floor PDFs or a folder> -o out/b
.venv/bin/python -m contract.validate_scene out/hall/scene.json
.venv/bin/python -m analysis out/hall/scene.json -o out/hall/analysis.json
.venv/bin/python -m sim run --scenario sim/examples/tiny_office_fire.json -o out/sim/x --seed 42 --decide cognition
.venv/bin/python -m loop run --scene S --scenario SC -o out/loop/x --decide cognition
.venv/bin/python -m cognition.serve --runs out/sim --port 8771
.venv/bin/python -m pytest -q tests
```

`--no-llm` runs the same drill on the built-in rules. Replays of those runs are free to scrub.

The demo script, URLs, and the fallback if a scan is missing are in [docs/DEMO.md](docs/DEMO.md).

## Already measured

Seed 42 unless noted.

- **30 synthetic LiDAR scans.** Up-axis within 2 degrees on every scan. Walkable overlap 0.958 / 0.914. Door recall 0.996, precision 0.982. Room count within one on every scan. Median wall error 6 mm. About 2 to 8 seconds a scan.
- **A real hall.** 14 by 14 meters, iPhone mesh, 1.67 million triangles ingested in 4.8 seconds. Curved walls kept. 19 people removed from the scan by OWLv2. 4 real exit signs found.
- **Tepper, CMU, floors 1 to 5.** 576 rooms, 915 doors, 29 exits, 12 stair links. Validator: 0 errors.
- **Tepper fire, 170 people.** No assisted-evacuation plan: 166 of 170 out, and 3 wheelchair users waiting at stair refuges on floor 4. With the plan: 169 of 170 out, and all 3 carried down by staff.
- **Hall, attacked.** 36 LLM occupants. Baseline 34 of 36 out. The adversary locks the exit 23 of them were using, 38.8 percent of the exit width: 15 of 36 get out. The optimizer unlocks it, posts 2 wardens, and adds signs: 33 of 36. 400 decisions, $0.086, about 0.94 seconds each.
- **Pre-movement.** Median 63.8 seconds, against a research target of about 60.
- **Interviews that can be wrong, and say so.** Removing the fire percept flips the cited choice in 7 of 7 re-runs. Removing a stated belief flips it in 0 of 7.

## How the pieces connect

```
iPhone RoomScanner (RoomPlan or LiDAR mesh)  --+
PDF floor plans (blueprint/, ingest_plans/)    +--> scene.json + display.glb
CMU Tepper sheets (tools/tepper_to_scene.py) --+        validated in contract/
        |
        v
analysis/     single points of failure, pinch points, smoke clock, occupant load
scenario/     plain language in, a checked scenario.json out
sim/          multi-floor crowd sim, stairs, refuges, replay
cognition/    the decision, the evidence check, the interview
loop/         adversary, then optimizer, then the measured report
flyagent/     the fruit-fly occupant, and the scrambled-wiring control
blueprint/viewer   the plan: replay, live drill, floors side by side, cutaway, interview
```

## Limits, stated plainly

- There is no mass-panic behavior. The evacuation literature treats it as rare. Delay comes from noticing, deciding, and getting ready to move.
- Fire and smoke block rooms and doors on a clock. There is no fire simulation. The smoke time is a closed-form estimate.
- Elevators are recalled in a fire. Someone may walk to the lobby and turn around.
- Walking speed and door flow follow standard limits. The decisions themselves are not a claim about a recorded evacuation of that building.
- Stragglers are a result. A run where everyone escapes is not the goal.

## Repo map

| Path | What |
|---|---|
| `contract/` | The scene format. Models, JSON Schema, validator, fixtures |
| `ingest_lidar/` | A scan to `scene.json`, `display.glb`, and debug images |
| `ingest_plans/` | Floor-plan PDFs to a multi-floor scene |
| `blueprint/` | Vector and raster plans, and the Three.js viewer |
| `ios/RoomScanner` | The iPhone app. RoomPlan and LiDAR mesh |
| `analysis/` | Failure points, pinch points, smoke clock, occupant load |
| `scenario/` | Scenario schema, resolver, chatbot |
| `sim/` | The crowd simulator, stairs, assisted evacuation, replay |
| `cognition/` | Decisions, the validator, the decision log, the interview server |
| `loop/` | Adversary and optimizer, scoreboard, `report.html` |
| `flyagent/` | Fruit-fly connectome occupant |
| `tools/` | Synthetic scans, scoring, plan rendering, the Tepper converter |
| `samples/`, `fixtures/` | Tepper, RoomPlan samples, test scenes |
| `scripts/demo/` | Setup, start, stop |
| `design/` | Tokens and the visual system |
| `docs/`, `research/` | Interfaces, cognition, the demo script, the research notes |

## Docs

- [docs/DEMO.md](docs/DEMO.md) — the three-minute demo
- [docs/SIM_INTERFACES.md](docs/SIM_INTERFACES.md) — scenario.json, decide(), replay.json
- [docs/AGENT_COGNITION.md](docs/AGENT_COGNITION.md) — how an occupant decides, and how an interview is checked
- [docs/LIVE_MODE.md](docs/LIVE_MODE.md) — the interactive drill
- [docs/pitch/](docs/pitch/) — the pitch and the writeup
- [research/](research/) — evacuation psychology, model comparisons, latency
- [docs/SCAN_GUIDE.md](docs/SCAN_GUIDE.md) — scanning a room
- [docs/DATA.md](docs/DATA.md) — sources, licenses, provenance
