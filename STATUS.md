# STATUS (WS-B, LiDAR -> scene)

Updated: 2026-11-07 ~12:30

## Works
- Contract (`contract/`): models, schema, validator, fixtures, negative fixtures.
- Path B (`python -m ingest_lidar scan.obj -o out/x`): OBJ/PLY/GLB/USDZ -> scene.json,
  display.glb (scan + walls_clean), navmesh_debug.glb, debug PNGs + GeoJSON.
- Path A (`python -m ingest_lidar structure.json -o out/x`, or a folder of
  RoomPlan room JSONs): RoomPlan CapturedRoom/CapturedStructure converter.
- iOS app `ios/RoomScanner` (RoomPlan, multi-room merge, export) compiles for device.
- LLM room labels via OpenRouter (cached; skipped with `--no-llm`).
- Exit tagging UI: `python -m tools.tag_exits out/x`.
- Synthetic benchmark: `python -m tools.make_synthetic_scan --bench data/synth` then
  `python -m tools.score_against_truth --bench data/bench -o out/bench`.

## Benchmark (30 synthetic scans, all targets met)
| metric | result | target |
|---|---|---|
| up error < 2 deg | 100% | 100% |
| walkable IoU (1.5 cm, 5% holes) | 0.956 | >= 0.90 |
| walkable IoU (3 cm, 10% holes) | 0.914 | >= 0.80 |
| door recall / precision | 0.979 / 0.961 | >= 0.85 / 0.80 |
| room count within +-1 | 96.7% | >= 85% |
| wall centerline median error | 0.012 m | < 0.10 m |
| obstacle recall (> 0.5 m) | 0.997 | >= 0.80 |
| runtime | <= 2.3 s (55k-150k tris) | <= 90 s at 500k |

## Verified end to end
- WS-A viewer (`blueprint/viewer`) opens our LiDAR output unchanged: copy
  `out/<run>/display.glb` and `scene.json` to `blueprint/viewer/public/lidar/`,
  `npx vite`, open `/?glb=/lidar/display.glb&scene=/lidar/scene.json`; clicking a
  room draws the path to an exit (26.3 m, 20.3 s on tiny_office).
- Runtime: 1.67M-triangle mesh in 4.8 s, 1.5 GB peak memory (target <= 90 s at 500k).

## Not done / known issues
- No real Path B scan yet (needs a human with an iPhone; see SCAN_GUIDE.md, ios/README.md).
- RoomPlan samples from homes produce many "exits" where doors lead to unscanned
  rooms; use exit_tags.json.
- Multi-level scans and Path B stairs: not implemented (stretch).

- Synthetic scans are Manhattan; diagonal walls are not emitted as clean walls.

## How to run
```
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # 66 passed in ~14 s
```

## Andrew's agent

- Andrew's agent: A1 demo setup done. `scripts/demo/setup.sh` (venv, requirements, npm ci,
  scenes, analyses, rule-policy replays incl. tepper_fire_noplan/plan, copies recorded
  `out/loop/demo_hall|demo_tepper` when present, builds a free offline tiny_office loop),
  `start.sh` (interview API :8771 + vite :5173, writes `out/demo/urls.txt`), `stop.sh`,
  `docs/DEMO.md`. Verified on Windows: start/stop, viewer proxy to the interview API, Tepper
  model loads with no console errors. Not verified: the full asset build (pip install fails
  on that machine, see docs/REQUESTS.md); run `scripts/demo/setup.sh` on a Python 3.11 laptop.
- Andrew's agent: README rewrite done.
- Andrew's agent: `docs/pitch/SUBMISSION.md` done. Pitch deck reassigned to another agent.
- Andrew's agent: A3 (`insights/`) not started; A1 and A2 were prioritized for the deadline.
