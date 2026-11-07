# Instructions for Andrew's agent (read fully, then start)

It is ~14:15. Feature freeze 16:00, fallback video recorded by 16:30, submission 17:00.
Repo: github.com/henhen1235/novaWinners, branch `main`. Python: repo-root `.venv`
(`python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt`). Node 20+.

## 1. What the project is (read this first)

**Rehearsal**: scan any building with an iPhone LiDAR (or upload a floor plan), get a
validated traversable 3D model, then stress-test its evacuation with LLM-driven occupants;
an adversary agent attacks the building, an optimizer agent proposes fixes, and the
simulator measures before/after. Goal: realistic simulation of what would happen, then
suggested alternatives. It is NOT "make everyone survive"; stragglers are findings.

Pipeline:
```
iPhone RoomScanner app (RoomPlan or LiDAR Mesh mode)  --+
CAD/PDF floor plans (WS-A blueprint pipeline)           +--> scene.json + display.glb
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

Read, in this order: `README.md` (WS-B only, outdated: you will rewrite it), `STATUS.md`,
`docs/SIM_INTERFACES.md`, `docs/AGENT_COGNITION.md`, `research/COMPETITIVE.md` (demo plan
and CLAIMS WE MUST NOT MAKE), `research/PITCH_IDEAS.md`, `research/EVAC_PSYCHOLOGY.md`,
`research/SCENARIOS_AND_MODELS.md`, `blueprint/AUDIT.md`, `design/DESIGN.md`.

Key numbers (verified today; quote exactly):
- LiDAR ingest, 30 synthetic scans: up-axis 100% < 2 deg, walkable IoU 0.958 / 0.914,
  door recall 0.996 / precision 0.982, rooms within +-1 on 100%, wall error 6 mm median,
  ~2-8 s per scan; 1.67M-triangle real mesh in 4.8 s.
- Real scan of a 14 x 14 m hall (iPhone Mesh mode): valid scene, curved walls traced,
  19 people removed from the scan by OWLv2 + LiDAR, 4 real exit signs detected.
- Tepper (CMU), floors 1-5: 576 rooms, 915 doors (28 inferred, flagged), 29 exits,
  12 stair links, scale from room sf labels (spread < 0.2%), validator 0 errors.
- Tepper fire, 170 rule-based occupants: no assisted-evacuation plan 166/170 out (3
  wheelchair users stranded at stair refuges on floor 4); with the plan 169/170 (all 3
  carried out by staff).
- Hall, adversary vs optimizer, 36 real LLM occupants (gemini-2.5-flash-lite): baseline
  34/36 out (t90 115.5 s); adversary blocked exit e_001 (23 users, 38.8% of exit width)
  -> 15/36 out; optimizer unlocked it, posted 2 wardens, added signage -> 33/36 out
  (t90 128 s); cost $0.086 for 400 decisions, ~0.94 s per decision; replays free.
- Tepper adversary vs optimizer with 60 LLM occupants: running now; read
  `out/loop/demo_tepper/report.md` when it exists (pull/ask Karthik).
- LLM calibration: pre-movement median 63.8 s (research target ~60 s), cue-based.
- Interview: "Why the West Exit?" -> cites logged evidence; removing the fire percept
  flips the choice 7/7 re-runs (necessary), a belief 0/7 (stated, not decisive).
- Model casting study: persona explains ~3x more of the choice than the model (304 calls).
- Fruit fly: real connectome flies 24/25 out, scrambled-wiring flies 0/20 (never moved).
Claims NOT to make: "first", "validated/certified/compliant", "panic", fire/smoke
physics, scale beyond what ran. Say "single run, seed 42" where true.

## 2. Ground rules

- `git pull --rebase --autostash origin main` before work and before every push; never
  force-push; stage files by explicit path only.
- Never stage `ios/RoomScanner/RoomScanner.xcodeproj/project.pbxproj`, `samples/scans/`,
  `data/`, `out/`, `blueprint/viewer/public/` (local demo assets), CMU drawings.
- Commits: `type(scope): imperative summary`, body says why; no AI attribution lines.
- Scene format frozen (`contract/`). Do not edit other owners' code (table below); write
  requests to `docs/REQUESTS.md` instead.
- No emoji, no em dashes, no puffery in docs.

| Area | Owner right now |
|---|---|
| `sim/`, `cognition/`, `loop/`, `scenario/`, `analysis/`, `ingest_lidar/`, `tools/tepper_to_scene.py`, `blueprint/viewer/src/` | Karthik's agents |
| `blueprint/ingest_blueprint`, `blueprint/geometry`, `blueprint/export` + Tepper work | Henry's agent |
| `validation/` (task T3 in HENRY_AGENT.md) | Henry's agent, after its Tepper work |
| **`scripts/demo/`, `docs/DEMO.md`, `docs/pitch/`, `README.md`** | **you (Andrew's agent)** |

## 3. Your tasks (in order)

### A1. One-command demo setup (`scripts/demo/`, `docs/DEMO.md`) - critical path

The demo assets exist only on Karthik's laptop. Make them reproducible from a clean clone:
1. `scripts/demo/setup.sh`: create `.venv` if missing, install `requirements.txt`
   (`requirements-cv.txt` only with `--cv`), `npm ci` in `blueprint/viewer`, then build into
   `blueprint/viewer/public/`:
   - `tepper/`: copy `samples/cmu/tepper/scene.json` + `display.glb`.
   - `fixtures/`: `fixtures/tiny_office.scene.json`, `fixtures/two_floor_school.scene.json`.
   - `hall/`: only if `samples/scans/hall_mesh/mesh.ply` exists (local): `.venv/bin/python -m
     ingest_lidar samples/scans/hall_mesh/mesh.ply -o out/hall --no-llm`, copy display.glb +
     scene.json; otherwise print a skip message.
   - `office/`: `(cd blueprint && ../.venv/bin/python -m ingest_blueprint samples/blueprints/office_vector/plan.pdf -o out/office --no-llm)`.
   - analyses: `.venv/bin/python -m analysis <scene> -o <dir>/analysis.json` (skip pinch on
     scenes > 150 rooms if slow; check `--help`).
   - replays (free, no LLM): `.venv/bin/python -m sim run --scenario sim/examples/<x>.json --scene <public scene> -o blueprint/viewer/public/replays/<x> --seed 42 --no-llm` for every
     example, plus `sim baseline` for tiny_office_fire and hall_alarm; Tepper with and
     without the assisted plan (`interventions: [{"type": "assisted_evacuation_plan"}]`).
   - recorded LLM runs: if `out/loop/demo_hall/` or `out/loop/demo_tepper/` exist, copy
     each stage (`r0_baseline`, `r1_attack`, `r1_fixed`, subfolder `s42`) to
     `public/replays/loop_<hall|tepper>_<stage>`. Re-running loop with the same command
     replays recorded decisions for free; do not trigger new LLM spend.
   Check every CLI's `--help` first; do not edit their code.
2. `scripts/demo/start.sh`: start `python -m cognition.serve --runs blueprint/viewer/public/replays --port 8771` and `npx vite --port 5173` in `blueprint/viewer`; wait until both answer; print and open the demo URLs. `stop.sh` stops both.
3. `docs/DEMO.md`: a 3-minute demo script with exact URLs and clicks, using these moments:
   (1) scan in, validated 3D out (hall, click a room for the route);
   (2) Tepper side-by-side floors replay (`?view=side`), then no-plan vs assisted-plan compare (`&compare=`);
   (3) adversary vs optimizer compare (attacked vs fixed) + open `report.html`;
   (4) click an occupant, ask "why did you go that way?" in the interview panel.
   Plus a fallback plan (pre-recorded video path, screenshots in `.playwright-mcp/`).
4. Smoke test with Playwright (headless): every URL loads, no console errors. Commit
   `feat(demo): ...`.

### A2. README, pitch deck, submission writeup (`README.md`, `docs/pitch/`)

1. Rewrite `README.md` for the whole project: one-sentence what/for whom; the pipeline
   diagram above; quickstart (`scripts/demo/setup.sh`, `start.sh`); where AI is used
   (LLM occupants with verified reasons, interviews, scenario chatbot, adversary +
   optimizer, room labeling, occupancy seeding, OWLv2 exit signs + people removal,
   Claude-assisted Tepper legend reading) and where it deliberately is not (geometry is
   computed, LLMs never output coordinates); repo map; links to docs/research. Factual.
2. `docs/pitch/index.html`: 7-8 slides, single self-contained HTML, `design/tokens.css`,
   arrow-key navigation. Structure from `research/PITCH_IDEAS.md` and
   `research/COMPETITIVE.md`: problem (drills are rare, delay not panic), what it does,
   capture (scan/plan to valid model in seconds), behaviourally grounded LLM occupants
   (PADM, research personas, calibration 63.8 s vs ~60 s), adversary vs optimizer with the
   hall scoreboard (and Tepper once available), accessibility finding (Tepper: wheelchair
   users stranded without a plan, carried out with it), interviews with verified reasons,
   honest limits + what's next. Use screenshots from `.playwright-mcp/` (copy the few you
   use into `docs/pitch/img/`, small PNGs only).
3. `docs/pitch/SUBMISSION.md`: inspiration, what it does, how we built it, challenges,
   accomplishments, what we learned, what's next, built with.
Commit `docs: ...`.

## 4. When done

Append "Andrew's agent: <task> done" lines to `STATUS.md`. If anything blocks you, write it
in `docs/REQUESTS.md` and push.

## 5. Added task A3 (do after A1, before A2): highest-ROI items from IDEAS.md

Karthik's agents are building live mode (docs/LIVE_MODE.md) and better people rendering;
do NOT touch sim/, cognition/, scenario/, blueprint/viewer/. Your package: `insights/`
(new, you own it) that turns any run directory (replay.json + decisions.jsonl +
metrics.json) into the evidence judges ask for:
1. RSET breakdown per occupant and per persona: time deciding (alarm to first move, plus
   later waits), walking, queuing (state queued), on stairs, waiting at refuge; stacked
   bar SVG. This is how fire engineers report egress (research/EVAC_PSYCHOLOGY.md).
2. Influence graph: who told whom (decision records cite messages `m_*` with `from`
   agent; rumor events), as a node-link SVG; highlight the rumor spread tree and how many
   it misled (metrics.misled_by_rumor).
3. Emotion timelines: fear/anxiety per agent over time from decision records (emotion
   field), building-level average curve, and the 5 most afraid occupants with their
   quoted `say` lines.
4. Per-persona outcomes table (out, median exit time, stragglers) and exit usage over
   time.
5. Faithfulness summary if interview results are cached (`cognition` interview outputs):
   share of stated reasons that were necessary / jointly sufficient / not decisive.
Output: `python -m insights <run_dir> [--compare <run_dir2>] -o <dir>` writing
insights.json + insights.html (single page, design/tokens.css). Run it on the recorded
demo runs (blueprint/viewer/public/replays/loop_hall_*, loop_tepper_*, tepper_fire_noplan,
tepper_fire_plan) and use the best charts in the deck (A2). Tests on a tiny fixture run.
Elevators: in fires they are recalled and not used; occupants may walk to an elevator
lobby and turn back. Mention this honestly in the deck as a modelling choice.

## 6. Note for the README and deck (added 14:50)

Product feature to document: the floor-plan converter (`python -m ingest_plans <floor
PDFs or folder> -o out/<name>`, being generalized from tools/tepper_to_scene.py right now;
see ingest_plans/README.md once pushed). It turns a building's CAD-exported floor-plan
PDFs into a validated multi-floor model in seconds (Tepper: 5 floors, 576 rooms; Mudge
dorm next). Source plans are never committed (CMU-internal); only our code and the
generated models are in the repo. Demo buildings: Tepper (main) and Mudge (dorm).

## 7. Demo framing (added 15:15, from Karthik)

Who uses it: the person accountable for a building's emergency plan. On a campus that
is environmental health and safety or emergency management, facilities, and residence
life; in an office, the facility or EHS manager. They already run drills, have floor
plans, and answer for the people who did not get out.

The gap: a drill happens in daytime, with every stair open, and mostly checks that
people know the routine. Nobody can drill the 3 a.m. dorm fire, the blocked main
stair, a building full of visitors, or the wheelchair user on floor 4. Rehearsal runs
those scenarios on a model of their own building and shows who gets left behind and
which change helps. Line: "You can't drill a 3 a.m. fire. You can rehearse one."

Thread for the pitch: in both demo buildings the people left inside were the ones who
cannot use stairs (Tepper: 3 wheelchair users at floor 4 refuges, 169/173 out, 172/173
with an assisted-evacuation plan; Mudge House night fire: 80/82, the 2 left are the
mobility-impaired residents at refuges on floors 2 and 3). Rule-based occupants, seed 42.

Demo order (Tepper is the main building, Mudge is the dorm):
1. Hook, no screen (20 s): the drill vs the night you cannot drill.
2. Building in (25 s): Tepper from CMU's own floor-plan PDFs: 5 floors, 633 rooms,
   22 exits, 12 stair links, validator 0 errors (`python -m ingest_plans`). One line on
   the iPhone scan for buildings without plans.
3. Weekday fire in Tepper (40 s): LLM occupants deciding (speech bubbles), the
   floor 4 refuge finding, then the assisted plan.
4. AI drill on Tepper (40 s): adversary attacks, optimizer picks fixes from a menu the
   facility can do, re-run proves it. Numbers: recorded LLM run on the corrected scene
   (baseline 55/60, attacked 41/60, fixed stage finishing about 15:30; use the final
   report in out/loop/demo_tepper/report.md).
5. Ask why (25 s): interview an occupant who stayed; reasons tested by removal.
6. Mudge House at 3 a.m. (20 s): Populate "Dorm night", click a fire into the common
   room, live; same finding in a second building.
7. Close (10 s): planning tool, not certification.

Update docs/DEMO.md and the deck to this order and these numbers. The old ones
(576 rooms, 29 exits, 166/170) are from before the Tepper reader fixes.
