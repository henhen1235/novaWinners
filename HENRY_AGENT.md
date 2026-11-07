# Instructions for Henry's agent (read fully, then start)

Deadline: submission 17:00 today, feature freeze 16:00. It is about 13:30 now.
Repo: github.com/henhen1235/novaWinners, branch `main`. Python env: repo-root
`.venv` (`python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt`).
Node 20+ for `blueprint/viewer`.

Do these four tasks in order (T1 and T2 first; they are on the demo's critical
path). Each is independent of the work other agents are doing right now, as long
as you stay inside the files listed under "You own".

## Ground rules

- Pull before work and before every push: `git pull --rebase --autostash origin main`.
  Many agents push to main every few minutes. Never force-push.
- Stage files by explicit path only (never `git add -A` / `git add .`).
- Never stage `ios/RoomScanner/RoomScanner.xcodeproj/project.pbxproj`,
  `samples/scans/`, `data/`, `out/`, or anything under `blueprint/viewer/public/`
  (large local demo assets).
- Never commit CMU floor plans (`data/cmu/raw/` is CMU-internal, gitignored).
- Commit messages: `type(scope): imperative summary`, body says why; no AI attribution lines.
- The scene format is frozen: `contract/models.py` + `contract/validate_scene.py`.
  Do not change it. Extra data goes under `extensions`.
- Run `.venv/bin/python -m pytest -q tests` before pushing Python changes.

## Who is editing what right now (do NOT edit these)

| Area | Files | Being changed by |
|---|---|---|
| Simulator | `sim/` | sim agent: multi-level, stairs, landing decisions, pace choice, level id in replay frames |
| Occupant reasoning + interviews | `cognition/` | done; calibration may follow |
| Adversary vs optimizer loop | `loop/` | loop agent (scoreboard, report.html, recorded real-LLM runs) |
| WS-A pipeline fixes | `blueprint/ingest_blueprint/`, `blueprint/geometry/`, `blueprint/export/`, `blueprint/tools/`, `blueprint/AUDIT.md`, `blueprint/FAILURES_CMU.md` | WS-A upgrade agent (curved walls done on raster; vector CAD path next) |
| Tepper building | `tools/cmu_*.py`, `samples/cmu/` | Tepper agent (floors 1-5 from CMU type sheets, multi-level scene) |
| LiDAR pipeline | `ingest_lidar/` | curved-wall agent |
| Scenario chatbot, analyses | `scenario/`, `analysis/` | done |
| Fruit fly (extra) | `flyagent/` | low priority |

Read before starting: `README.md`, `docs/SIM_INTERFACES.md` (replay.json, decide()
hook, scenario.json), `docs/AGENT_COGNITION.md`, `research/COMPETITIVE.md` (demo plan,
claims to avoid), `research/PITCH_IDEAS.md`, `design/DESIGN.md` (visual system).

## T1. Viewer: multi-floor buildings  [TAKEN by Karthik's agent at 13:50; skip T1, start at T2, and do not edit `blueprint/viewer/`]

The demo building (Tepper, 5 floors) is multi-level. Replay frames are getting a 5th
element with the level id: `["a_01", x, z, state, "L2"]` (older replays have 4
elements; treat missing level as the scene's first level).

1. Floor views (the user asked for these specifically; this is the most visible part of
   the demo). Add a view switch with four modes, sharing one camera control and the
   replay clock:
   - **Single floor** (existing `Level` dropdown): only that level's geometry, navmesh,
     rooms, doors and agents.
   - **Side by side** (default for multi-level replays): every level laid flat in a
     row or grid (auto layout by aspect ratio, ~2 m gap scaled to building size), each
     with a caption ("Floor 3 - 12 people") and its own live count; top-down
     orthographic camera; stair links drawn as small connector badges between panels
     (same color per stair id) and agents on stairs shown at the badge. This is the
     view for watching the simulation.
   - **Stacked (real-life)**: levels at their `elevation_m`; walls rendered
     semi-transparent (opacity ~0.15, depth write off) with crisp edge outlines so
     occupants stay visible; non-focused floors fade to ghost outlines, the hovered or
     selected floor becomes solid; agents always drawn on top (depthTest off or a
     separate render pass) with a soft glow; people on stairs drawn along the stair link
     between levels. Optional section cut: a slider that clips everything above a
     height (three.js clipping planes) to look into a floor.
   - **Exploded**: stacked view with a vertical spacing slider (1x to 3x floor height)
     so floors separate, same transparency rules.
   Readability (user feedback: "each floor feels like a massive block"): never draw
   ceilings or roofs; floors are a thin light slab with rooms lightly tinted by type;
   add a Walls toggle `off | cutaway | full` with **cutaway as the default**: walls
   clipped at 1.2 m above each floor's elevation (per-level clipping plane or a
   geometry rebuild from scene.json walls), so you look down into every room and see
   people; the cutaway applies per floor in stacked/exploded views. If a display.glb
   contains slabs or tall meshes, apply the same per-level clip to it (or hide its wall
   nodes and draw walls from scene.json). Persist as `&walls=off|cutaway|full`.
   Persist the chosen mode in the URL (`&view=side|stacked|exploded|single`). Check
   how professional tools present multi-floor evacuations (e.g. Thunderhead Pathfinder
   and Oasys MassMotion screenshots/docs: stacked floors with transparency, per-floor
   2D views) and match those conventions. Declutter room labels: show labels only above
   a zoom threshold or for rooms > 30 m^2 when zoomed out (Tepper has 580 rooms).
2. Agents: draw each agent on its frame's level (y = level elevation_m); hide agents
   not on the selected level in single-floor mode; show a per-floor count
   ("L3: 12 people") in the rail.
3. Stairs: draw `stairs[]` polygons and `links[]` of type stairs as a ramp/arrow between
   levels; agents in state `on_stairs` (or between levels) are drawn along the link.
   New states to color (add tokens if missing): `on_stairs`, `waiting_at_refuge`.
4. Route-on-click must work across floors (A* over navmesh triangles of all levels plus
   stair links; cost = link `cost_multiplier` x length).
5. Test data: `fixtures/two_floor_school.scene.json` (2 levels, 2 stairs). Extend
   `scripts/fake_replay.ts` with `--levels` so fake agents start upstairs and walk down
   the stairs. Verify with screenshots; no console errors. Then, when
   `samples/cmu/tepper/` appears (pull), load it.
6. Commit as `feat(viewer): ...`.

## T2. One-command demo setup (you own `scripts/demo/`, `docs/DEMO.md`)

The demo assets currently exist only on one laptop. Make it reproducible:

1. `scripts/demo/setup.sh`: from a clean clone, create `.venv` if missing, install
   `requirements.txt` (and `requirements-cv.txt` only with `--cv`), `npm ci` in
   `blueprint/viewer`, then build every demo asset into `blueprint/viewer/public/`:
   - hall scan scene: `.venv/bin/python -m ingest_lidar samples/scans/hall_mesh/mesh.ply -o out/hall --no-llm` only if that file exists (it is local); otherwise skip with a message.
   - fixtures: `fixtures/tiny_office.scene.json`, `fixtures/two_floor_school.scene.json`.
   - Tepper: `samples/cmu/tepper/` if present.
   - blueprint office: `(cd blueprint && ../.venv/bin/python -m ingest_blueprint samples/blueprints/office_vector/plan.pdf -o out/office --no-llm)`.
   - analyses: `.venv/bin/python -m analysis <scene> -o <dir>/analysis.json` for each.
   - sim runs: `.venv/bin/python -m sim baseline ...` and `.venv/bin/python -m sim run ... --decide cognition --no-llm`
     for each demo scene and scenario (see `sim/examples/`); plus replay of any recorded
     real-LLM runs from `out/loop/` (`NOVA_REPLAY=<decisions.jsonl>` replays recorded LLM
     decisions for free; see cognition docs).
   Check `python -m sim --help` and `python -m analysis --help` for exact flags; do not
   edit sim/ or analysis/.
2. `scripts/demo/start.sh`: start `python -m cognition.serve --runs blueprint/viewer/public/replays --port 8770`
   and `npx vite` in `blueprint/viewer` (port 5173), wait until both answer, then print
   and open the three demo URLs (scan to route, classic vs LLM side by side, interview).
   `scripts/demo/stop.sh` stops both.
3. `docs/DEMO.md`: the 3-minute demo script (moments from `research/COMPETITIVE.md`),
   exact URLs, what to click, what to say, and a fallback plan (recorded video path).
4. Smoke test: run setup + start on this machine, open each URL headless (Playwright)
   and confirm no console errors. Commit as `feat(demo): ...`.

## T3. Credibility: validation against published values (you own `validation/`, `tests/test_validation.py`)

Judges will ask "is this real?". Build `validation/` that runs the simulator as a black
box (`python -m sim baseline/run` with generated scenes and scenarios) on standard
evacuation-model test cases and compares to published values:

1. Free walking speed: 1 agent, 40 m straight corridor (2 m wide); compare mean speed per
   persona to the model inputs and to Weidmann 1.34 m/s (sd 0.26) (see
   `research/EVAC_PSYCHOLOGY.md`). IMO MSC.1/Circ.1533 Test 1 style.
2. Door flow: 100 agents in an 8 x 5 m room, one 1.0 m exit; specific flow must be
   <= ~1.33 persons/s/m (IMO Test 4 style; SFPE ~1.3 p/s/m). Report the measured flow.
3. Pre-movement time distribution: 50 office agents, alarm only, rule policy and (if
   cheap and recorded) cognition; compare median/p90 to the lognormal ranges in
   `research/EVAC_PSYCHOLOGY.md`.
4. Familiar-exit imbalance (Station fire analogue, `research/EVAC_PSYCHOLOGY.md` test 2):
   all agents entered by the main door, two equal side exits; report the share using
   the main door (literature ~2/3) vs the shortest-path baseline.
Build scenes with `tools/layouts.py` (`build_scene(spec)`; see its fixtures) so they
validate. Output `validation/report.md` + an SVG chart per test + one summary table
(test, metric, our value, reference, pass/fail, source). Mark references that are
unverified as such. `python -m validation run -o out/validation`. Tests check the
harness runs on a tiny case (< 20 s). Commit as `feat(validation): ...`.

## T4. Pitch deck, README, submission writeup (you own `docs/pitch/`, `README.md`)

1. Rewrite the top-level `README.md` for the whole project (currently WS-B only): one
   sentence of what it is; architecture (capture: iPhone RoomPlan/Mesh app + blueprint
   PDFs -> validated scene.json -> sim with LLM occupants -> analyses, adversary vs
   optimizer -> viewer + interviews); quickstart (`scripts/demo/setup.sh`,
   `start.sh`); where AI is used (occupant decisions, interviews with verified reasons,
   scenario chatbot, room labeling, occupancy seeding, adversary/optimizer, exit-sign
   detection with OWLv2) and where it deliberately is not (geometry); repo map; links
   to `docs/` and `research/`. Keep it factual, no puffery, no emoji, no em dashes.
2. `docs/pitch/`: a 6-8 slide deck as a single self-contained HTML file using
   `design/tokens.css` (keyboard navigation), following `research/COMPETITIVE.md`
   (differentiators that survive scrutiny, claims we must NOT make: not "first", not
   "validated/certified", no "panic", no smoke physics claims) and
   `research/PITCH_IDEAS.md` (positioning sentence, 3-minute narrative). Include the
   numbers from `STATUS.md` (scan benchmark), the validation table from T3, and the
   adversary/optimizer scoreboard from `out/loop/*/scoreboard.json` once it exists
   (leave a clearly marked placeholder until then).
3. `docs/pitch/SUBMISSION.md`: the submission writeup (inspiration, what it does, how
   we built it, challenges, accomplishments, what we learned, what's next, built with).
Commit as `docs: ...`.

## Coordination

- If you need a change in a file owned by another agent (table above), do not edit it:
  write the request in `docs/REQUESTS.md` (who, what, why) and push it; Karthik's
  agents read it.
- Status: append a short line per finished task to `STATUS.md` under a "Henry's agent"
  heading.
