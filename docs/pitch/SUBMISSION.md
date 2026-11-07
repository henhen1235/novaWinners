# Rehearsal

Scan a building with an iPhone or load its floor plans, get a validated 3D model in
seconds, and run an evacuation drill in it with LLM-driven occupants. An adversary agent
attacks the building, an optimizer agent proposes fixes, and the simulator measures
before and after.

## Inspiration

Fire drills test whether people know the drill. They do not test what happens when the
main exit is locked and someone says the other one is too. The evacuation literature is
consistent on why real evacuations go wrong: not panic, which is rare, but delay. People
wait for confirmation, finish what they were doing, go back the way they came in, and
believe what they hear. Professional egress tools (Pathfinder, MassMotion, buildingEXODUS)
are good at crowd flow, but they need a CAD model that takes days to build and they encode
behavior as parameters set in advance. Most buildings never get modeled at all. We wanted a
drill you can run on any building, with occupants that react to what they see and hear, and
that you can question afterwards.

## What it does

1. Capture. An iPhone app (RoomPlan or LiDAR Mesh mode) or a set of CAD-exported floor-plan
   PDFs becomes a `scene.json` plus a 3D model: walls, rooms, doors, exits, stairs and a
   walkable navmesh, checked by a shared validator.
2. Analysis. Single points of failure, pinch points, occupant load and a smoke-clock
   estimate per room, before anyone is simulated.
3. Scenario. Describe the emergency in plain language ("fire in room 3500, smoke in the
   west stairwell"); a chatbot turns it into a validated scenario.
4. Simulation. A navmesh crowd simulator moves people on a fixed 0.5 s clock across floors
   and stairs, with door-flow limits, stair refuges and staff who carry people who cannot
   use stairs. At decision points (alarm, blocked path, smoke, a landing, someone needs
   help) each occupant's decision is made by an LLM.
5. Adversary vs optimizer. One agent picks failures with a number-backed reason (lock the
   busiest exit, plant a rumor); another picks fixes from a menu the code can execute
   (warden, signage, PA announcement, unlock, assisted-evacuation plan, widen a door). Every
   patch is validated, then re-run, and a scoreboard and report compare the rounds.
6. Viewer and interviews. A Three.js viewer replays runs with floors side by side, stacked
   or exploded, compares two runs, and lets you click an occupant and ask why it went that
   way. The answer comes only from its decision log, and each reason is tested.

## How we built it

- Scene contract (`contract/`): one frozen format and validator shared by every capture
  path, the simulator, scenarios and optimizer patches.
- Capture: Python with trimesh, shapely and OpenCV for mesh and plan geometry; OWLv2 on scan
  keyframes to detect exit signs and remove people from the scan; an LLM for room labels
  and occupancy seeding; a Swift RoomPlan app; a floor-plan converter for multi-floor PDFs
  (Tepper, with Claude helping read the plan legend).
- Simulator (`sim/`): A* over navmesh triangles with string pulling, per-persona speeds,
  queuing at doors, stair speeds and stair flow, refuges and assisted evacuation; output is
  a deterministic replay plus a decision log.
- Occupant cognition (`cognition/`): perception, beliefs, appraisal and emotion are computed
  in code from published effects; the LLM answers the Protective Action Decision Model
  questions and must cite evidence ids that a validator checks. Invalid output is repaired
  once, then falls back to a persona rule. Every LLM response is recorded, so replays are
  exact and free. Occupants ran on gemini-2.5-flash-lite through OpenRouter.
- Interviews: retrieve the relevant decision, re-run it with each cited reason removed to
  test whether it was necessary, then compose an answer in the occupant's voice and drop
  any sentence that does not cite a record.
- Loop (`loop/`): adversary and optimizer agents with constrained action menus, patch
  validation, multi-seed scoreboard and a self-contained HTML report.
- Viewer: TypeScript, Three.js and Vite, with a design system shared by the viewer, plan
  renderer and iOS app.

## Results (single runs, seed 42, unless noted)

- LiDAR ingest on 30 synthetic scans: door recall 0.996, precision 0.982, walkable IoU
  0.958, median wall error 6 mm, about 2 to 8 s per scan; a 1.67M-triangle real mesh in 4.8 s.
- Real 14 x 14 m hall scanned in Mesh mode: valid scene, curved walls traced, 19 people
  removed from the scan, 4 exit signs detected.
- Tepper (CMU), floors 1 to 5 from floor plans: 576 rooms, 915 doors (28 inferred and
  flagged), 29 exits, 12 stair links, 0 validator errors.
- Tepper fire with 170 rule-based occupants: without an assisted-evacuation plan 166/170 get
  out and 3 wheelchair users are left waiting at stair refuges on floor 4; with the plan
  169/170, and staff carry all 3 out.
- Hall with 36 LLM occupants: baseline 34/36 out. The adversary locked the exit used by 23
  people (38.8 percent of exit width): 15/36. The optimizer unlocked it, posted 2 wardens
  and added signage: 33/36. 400 LLM decisions cost $0.086, about 0.94 s each.
- LLM occupants' median pre-movement time 63.8 s, against a research target of about 60 s.
- Interview reasons tested by removal: removing the fire percept flips the choice in 7 of 7
  re-runs (necessary); removing a stated belief flips it in 0 of 7 (stated, not decisive).
- Model casting study (304 calls): the persona explains about 3 times more of the choice
  than the choice of model.

## Challenges we ran into

- Real scans are messy: curved walls, people standing in the room, doors that lead to
  unscanned space. We added curved-wall tracing and OWLv2 plus LiDAR people removal.
- Multi-floor buildings: stairs, landings, refuges and people who cannot use stairs needed
  new simulator states, decision triggers and viewer modes so five floors stay readable.
- Faithful explanations: asking a model why it did something produces a plausible story, not
  the cause. We record reasons at decision time and test them by intervention.
- Keeping LLM agents realistic: uncalibrated, they either evacuate instantly or wait
  forever. Cue-based waiting rules brought the pre-movement median to 63.8 s.
- Many agents pushing to one branch for a day, held together by one frozen scene contract.

## Accomplishments that we're proud of

- A real building (Tepper, five floors) and a real scan (the hall) running end to end.
- An accessibility finding the averages hide: wheelchair users left at stair refuges
  without a plan, and carried out with one.
- An adversary and optimizer whose fixes are executable and measured, at under 9 cents for
  a 36-person run.
- Interview answers that say which of their own reasons actually mattered.

## What we learned

- Decisions, not walking speed, drive evacuation outcomes; delay is the main effect.
- "Model proposes, code checks" is what makes LLM agents usable: the LLM never outputs
  coordinates, and every choice is validated against offered options and evidence.
- Recording every LLM response makes runs reproducible and replays free.
- Persona matters more than which model plays it.

## What's next

- Validate movement against standard test cases (free walking speed, door flow) and occupant
  decisions against published drill data.
- More seeds per run with ranges on every number.
- Live mode: add events and people while watching, branching from the recorded run.
- More buildings from floor plans (next: a dormitory) and live scans of the judging room.

## Limits

Rehearsal is a planning tool. It does not certify a building and decision behavior is not
validated against real evacuations. Fire and smoke are scheduled blocking events, not fire
physics. Elevators are recalled in fires and not used. Numbers above are single runs unless
noted.

## Built with

Python, NumPy, SciPy, shapely, trimesh, networkx, OpenCV, scikit-image, PyMuPDF, ezdxf,
ifcopenshell, usd-core, pydantic, FastAPI, OWLv2 (transformers, PyTorch), OpenRouter,
gemini-2.5-flash-lite, Claude, TypeScript, Three.js, Vite, Swift, ARKit RoomPlan.
