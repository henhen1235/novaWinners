# PLAN: WS-B, LiDAR scan to traversable scene

Scope: scene building from a LiDAR scan only. Output per scan: `scene.json`
(frozen contract in `contract/`) plus `display.glb`, `navmesh_debug.glb` and a
debug PNG set. Simulation, LLM occupants, adversary and optimizer are WS-C.

## Milestones (in order; each ends with green tests, a commit, STATUS.md)

| M | Deliverable | Done when |
|---|-------------|-----------|
| M0 | `contract/` (pydantic models, generated JSON Schema, validator), fixtures `tiny_office`, `two_floor_school`, one negative fixture per validator error code, repo skeleton | validator exits 0 on both fixtures and non-zero with the expected code on every negative |
| M1 | Vertical slice: `tools/make_synthetic_scan.py` -> `python -m ingest_lidar scan.obj -o out/x` -> load, orient, occupancy, walkable, exit openings, navmesh, valid `scene.json` + `display.glb` | a synthetic scan of `tiny_office` validates with exit code 0 |
| M2 | walls, rooms, interior doors, obstacles; `tools/score_against_truth.py`; 30 seeded synthetic scans | targets in section "Scoring" met on the benchmark |
| M3 | (parallel, subagent) `ios/RoomScanner` Swift app + `ios/README.md`; `ingest_lidar/roomplan_to_scene.py` tested on a sample CapturedRoom/CapturedStructure JSON | converter output validates |
| M4 | first real one-room scan end to end; `tools/tag_exits.py` click-to-tag flow; `--corrections` | real scan validates |
| M5 | full-floor real scan; performance (<= 90 s on 500k triangles); stairs (stretch) | |
| M6 | hardening: hole handling, warning quality, README, SCAN_GUIDE, demo script | |

## Pipeline (Path B, generic mesh)

`load -> orient -> occupancy -> walkable -> walls -> rooms/doors -> obstacles -> exits -> navmesh/export -> display`

- orient: Manhattan frame from area-weighted face normals; the up axis is
  scored by (a) two dominant extreme planes, (b) furniture-top asymmetry
  (horizontal faces between floor and ceiling face up, never down), (c)
  floor-to-ceiling extent in [2.0, 4.8] m, (d) a small prior for the file's own
  Y. Output is a proper rotation (det +1), recorded in
  `extensions.lidar.input_to_scene` so scoring can undo it.
- occupancy: area-weighted surface samples, 5 cm XZ grid, heights relative to a
  per-2 m-tile floor estimate (handles floor drift). Bands: floor, body, upper wall.
- walkable: floor_supported and not occupied; enclosed small unknown pockets
  are filled; outside-connected unknown is never walkable; erode by 0.25 m.
- walls: cells that are occupied in the body band AND reach the upper band
  (walls touch the ceiling, furniture does not), kept only if part of a long
  axis-aligned run (morphological opening with a line element).
- doors: gaps along a wall line of 0.6 to 2.2 m that are free at body height.
  Exit doors: places where floor meets outside-connected unknown over 0.6 to 3 m.
- rooms: connected components of interior space with walls and door segments
  as barriers.
- obstacles: non-wall body-band blobs.

## AI usage (OpenRouter)

Semantics only, never coordinates. Cached on disk by SHA-256 of (model,
prompt, image bytes). Every stage runs with `--no-llm`. Uses: room type and
name from geometry descriptors, exit sign text from photos listed in
`scan_notes.json`. Key: `OPENROUTER_API_KEY` from `.env`; model from
`OPENROUTER_MODEL` (default `anthropic/claude-haiku-5.5`). The key has a $7
limit shared with WS-C, whose LLM occupants need most of it.

Development also uses AI agents: parallel subagents for the iOS app, the
RoomPlan converter and method research.

## Risks

1. Up-axis ambiguity on box-shaped single rooms. Mitigation: furniture
   asymmetry cue, prior for the file's Y, `--up` override, scores in debug output.
2. Scan holes at doors disconnect rooms. Mitigation: fill enclosed unknown
   pockets, UNKNOWN_SPACE_NEAR_DOOR warning, `--corrections` add-door.
3. Validator requires every navmesh triangle to reach an exit. Mitigation:
   trim exit-unreachable walkable pockets with a warning.
4. No LiDAR device available to the agent. Mitigation: synthetic scans first;
   real scans need a human (SCAN_GUIDE.md, ios/README.md).
5. Small LLM budget. Mitigation: cache, cheap default model, `--no-llm` path.

## Test plan

- `tests/test_contract.py`: schema matches models; fixtures pass; each negative
  fixture fails with its code.
- Unit tests per module on small synthetic meshes (box rooms, L-shaped room to
  prove no mirroring, random rotations for orient).
- `@pytest.mark.slow`: synthetic scan end to end, determinism (byte-identical
  scene.json except `generated_at`).
- Benchmark (`python -m tools.score_against_truth`): 30 seeded scans, report
  vs targets below.

## Scoring targets

Up axis error < 2 deg on 100%; walkable IoU >= 0.90 (sigma 1.5 cm, 5% holes)
and >= 0.80 (3 cm, 10%); door recall >= 0.85, precision >= 0.80; room count
within +-1 on >= 85%; wall centerline median error < 0.10 m; obstacle recall
>= 0.80 for objects > 0.5 m tall; 100% rooms reach an exit; <= 90 s for 500k
triangles; deterministic.
