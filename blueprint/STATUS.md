# Status

## What works

- Frozen scene contract, now the shared repo-root `contract/` package (root DECISIONS.md D16 to D18), and `python -m contract.validate_scene`. Positive fixtures and one negative fixture per code.
- Vector PDF ingest, including double-line walls. `python -m ingest_blueprint samples/blueprints/office_vector/plan.pdf -o out/office --no-llm` exits 0 and the validator prints no issues.
- Fifty synthetic vector plans meet the score gate: walkable IoU at least 0.92, door recall at least 0.90, door precision at least 0.85, room count within 1 on every plan, exit recall at least 0.95, scale error within 3% when a scale bar or dimension is drawn and within 12% on door-reference plans, and every room reaches an exit. Five seeds run in the default test suite. All fifty are `pytest -m slow`.
- Corrections in source-image pixels. Removing an interior door makes a room unreachable; adding it back clears the validator. `python tools/path_probe.py` prints a finite distance.
- FastAPI editor at `python -m editor.app`. The canvas posts pixel operations and re-runs with `--no-llm`.
- Three.js viewer: orbit, top-down, level select, overlays, click-to-exit path.
- Multi-page plans: one level per page, 3.0 m elevation step, stair links with `cost_multiplier` 2.0. A single-level stairwell is an exit with `leads_to: stairwell`.
- DXF (`A-WALL`, `A-DOOR`, lines, polylines, arcs, inserts) through the same pipeline.
- `--no-llm` is the default safe path. Vision labels, when a key is set, are cached under `out/llm_cache/` by SHA-256 of model, prompt, and image bytes. Coordinates from the model are only a snap hint.
- Raster pages (no vectors) use a sealed wall mask and recover the room count within 1 on the single-line synthetic sheets, with walkable IoU around 0.80–0.92.

## What does not work

- Raster score thresholds are not met. Door recall stays under 0.80, and the navmesh often has a component that does not touch an exit. Double-line raster sheets (hairline strokes) do worse. See DECISIONS.
- No redistributable evacuation-map PDF or buildingSMART IFC file was downloaded. IFC loading is implemented behind `requirements-ifc.txt` and is untested on a real file.
- CubiCasa weights are not present. `--ml` is a no-op and classical segmentation runs.
- The viewer does not bake text into the glTF. Room names are in `scene.json` and on `overlay.png`.

## How to run

```bash
source .venv/bin/activate
pip install -r requirements.txt
python -m ingest_blueprint samples/blueprints/office_vector/plan.pdf -o out/office --no-llm
(cd .. && python -m contract.validate_scene blueprint/out/office/scene.json)
pytest
pytest -m slow
```

Python 3.11.17 via `uv`. The viewer is `cd viewer && npm install && npm run dev` after copying `out/office/display.glb` and `scene.json` into `viewer/public/office/`.

## Known issues

- A raster page with no vector text names rooms `Room` and can miss an exit, which fails the validator even when the room outlines are close.
- `generated_at` changes between runs. Every other scene field is deterministic for a given input.
