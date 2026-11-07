# Rehearsal

Scan a building with an iPhone (LiDAR) or load a blueprint, get a traversable
3D scene, and stress-test its evacuation with LLM-driven occupants. This repo
holds the shared scene contract (`contract/`) and the LiDAR ingest (WS-B).

## Quickstart (LiDAR path, synthetic scan, no device needed)
```
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tools.make_synthetic_scan --layout tiny_office --seed 0 -o data/synth/demo
.venv/bin/python -m ingest_lidar data/synth/demo/scan.obj -o out/demo --no-llm
```
Output in `out/demo/`: `scene.json` (validated), `display.glb`, `navmesh_debug.glb`,
`debug/overview.png`. Check any scene with
`.venv/bin/python -m contract.validate_scene out/demo/scene.json`.

## Real scans
- iPhone app: `ios/README.md` (RoomPlan; exports `structure.json`).
  `python -m ingest_lidar samples/scans/<name>/structure.json -o out/<name>`
- Any mesh (Polycam, 3D Scanner App: OBJ/PLY/GLB/USDZ in meters):
  `python -m ingest_lidar <file> -o out/<name>`
- How to scan: `SCAN_GUIDE.md`. Fix or name exits: `python -m tools.tag_exits out/<name>`.

## Options
`--no-llm` skips LLM room labels. LLM uses `OPENROUTER_API_KEY` (from env or
`.env`) and `OPENROUTER_MODEL` (default `anthropic/claude-haiku-5.5`).
`--up +y` forces the up axis, `--scale 0.01` fixes centimeter files,
`--exit-tags tags.json` adds exits.

## Docs
`PLAN.md` (milestones, algorithms), `DECISIONS.md`, `STATUS.md`, `DATA.md`,
`research/` (latency, pitch, evacuation psychology, test data).

## Tests
`.venv/bin/python -m pytest -q` (slow tests: `-m slow`).
