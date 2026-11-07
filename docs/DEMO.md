# Demo: 3 minutes, four moments

## Before the pitch (once per laptop)

```bash
scripts/demo/setup.sh            # .venv, requirements, npm ci, builds blueprint/viewer/public/
scripts/demo/start.sh            # interview API :8771 + viewer :5173, prints and opens the URLs
scripts/demo/stop.sh             # afterwards
```

`setup.sh --skip-install` rebuilds only the assets. It makes no LLM calls:
replays use the rule policy (seed 42), and recorded LLM runs are copied from
`out/loop/demo_hall/` and `out/loop/demo_tepper/` when those folders exist.

Copy these from the laptop that has them before running setup, or the
matching moment falls back as noted:

| Local file (gitignored) | Needed for | Without it |
|---|---|---|
| `samples/scans/hall_mesh/mesh.ply` | Moment 1 on the real hall scan | Moment 1 uses the office blueprint |
| `out/loop/demo_hall/` (recorded LLM loop, 36 occupants) | Moments 3 and 4 with real LLM occupants | Offline tiny_office loop (heuristic adversary/optimizer, rule occupants); say so |
| `out/loop/demo_tepper/` | Optional Tepper loop | Skipped |
| `.env` with `OPENROUTER_API_KEY`, and `data/llm_cache/` | Moment 4 interview answers | Interview panel shows an error; use the fallback screenshot |

`start.sh` writes the exact URLs for this machine to `out/demo/urls.txt`.
All URLs below assume the viewer on `http://localhost:5173`.

## Script

### 0:00-0:20 Hook (no screen)

"Fire drills test whether people know the drill. Real evacuations go wrong on
decisions: people wait, go back the way they came in, believe what they hear.
Most buildings never get an evacuation model because building one takes days of
CAD work."

### 0:20-0:50 Moment 1: scan in, validated 3D out

Open (hall scan):
`http://localhost:5173/?glb=/hall/display.glb&scene=/hall/scene.json&analysis=/hall/analysis.json`
(no hall on this laptop: `?glb=/office/display.glb&scene=/office/scene.json&analysis=/office/analysis.json`)

Click: press **Top**, then click the floor of a far room. The green route draws
to the nearest exit with meters and seconds. Point at the title block: source,
rooms, exits, "0 warnings".

Say: "This is a 14 by 14 meter hall scanned with an iPhone in Mesh mode. The
pipeline traced the curved walls, removed the 19 people who were in the scan
with OWLv2 plus LiDAR, and found 4 real exit signs. The validator passes it.
On 30 synthetic scans door recall is 0.996 and ingest takes 2 to 8 seconds."

### 0:50-1:30 Moment 2: Tepper, five floors, the accessibility finding

Open (side by side floors):
`http://localhost:5173/?glb=/tepper/display.glb&scene=/tepper/scene.json&replay=/replays/tepper_fire_noplan/replay.json&view=side`

Click: play, speed 16x. Point at the per-floor counts and the stair badges.

Say: "Tepper at CMU, floors 1 to 5, built from the facilities sheets: 576
rooms, 915 doors, 29 exits, 12 stair links. Fire on floor 3, 170 occupants."

Then open (no plan vs assisted plan):
`http://localhost:5173/?glb=/tepper/display.glb&scene=/tepper/scene.json&replay=/replays/tepper_fire_noplan/replay.json&compare=/replays/tepper_fire_plan/replay.json&names=No%20plan,Assisted%20plan&view=side`

Click: play to the end; point at the floor 4 stair refuge on the left.

Say: "Without an assisted-evacuation plan 166 of 170 get out; the 3 wheelchair
users wait at stair refuges on floor 4. With the plan staff carry all 3 out:
169 of 170. Rule-based occupants, single run, seed 42."

### 1:30-2:15 Moment 3: adversary vs optimizer

Open (attacked vs fixed, recorded LLM hall run):
`http://localhost:5173/?scene=/hall/scene.json&glb=/hall/display.glb&replay=/replays/loop_hall_r1_attack/replay.json&compare=/replays/loop_hall_r1_fixed/replay.json&names=Attacked,Fixed`

Then open the report: `http://localhost:5173/loop/demo_hall/report.html`

Say: "36 LLM occupants on gemini-2.5-flash-lite. Baseline: 34 of 36 out. The
adversary locked exit e_001, the one 23 people used and 38.8 percent of the
exit width: 15 of 36 out. The optimizer can only pick from a menu the code can
execute: it unlocked the exit, posted 2 wardens and added signage. Re-run: 33
of 36 out. 400 decisions cost 8.6 cents; replays are free."

Offline fallback (no `out/loop/demo_hall`):
`http://localhost:5173/?scene=/fixtures/tiny_office.scene.json&glb=&replay=/replays/loop_tiny_r1_attack/replay.json&compare=/replays/loop_tiny_r1_fixed/replay.json&names=Attacked,Fixed`
and `http://localhost:5173/loop/offline_tiny/report.html`. Say "offline run,
heuristic adversary and rule occupants" and quote the hall numbers above as
the recorded LLM run.

### 2:15-2:45 Moment 4: interview

Open (fixed hall run):
`http://localhost:5173/?scene=/hall/scene.json&glb=/hall/display.glb&replay=/replays/loop_hall_r1_fixed/replay.json`

Click: pause mid-run, click an occupant, type "Why did you go that way?" in the
interview panel.

Say: "Every answer cites a logged decision. We test the reasons by removing
them: take away the fire percept and the choice flips in 7 of 7 re-runs, so it
was necessary; remove a stated belief and it flips 0 of 7, so it was stated
but not decisive. The answer says which."

### 2:45-3:00 Close

"Rehearsal is a planning tool, not a certification. It shows where people
hesitate, who gets left behind, and which fix helps, in minutes."

## Fallback plan

- Recorded video: record by 16:30 to `out/demo/fallback.mp4` on the presenting
  laptop (gitignored, too large for git) and keep a copy on the team drive.
  Record the four moments above in order from a working `start.sh`.
- Screenshots: `.playwright-mcp/` on the laptop that ran the browser tests
  (gitignored); the pitch deck in `docs/pitch/index.html` has the key numbers
  and works offline.
- If the viewer fails live: open `docs/pitch/index.html` and walk the numbers.
- If only the interview fails (no key or network): show the answer from the
  video and say the API is local and needs network for uncached questions.

## Smoke test

With the servers up, each URL above should load with no console errors. On
the Windows test laptop (Python 3.14, no 3.11) `pip install -r requirements.txt`
fails on the pinned `certifi==2026.10.5` and on `scikit-image==0.25.2` (no
wheel), so only the scene-only views were checked there; see `STATUS.md`.
