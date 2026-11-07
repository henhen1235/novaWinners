# Rehearsal (WS-A)

Turn a 2D floor plan into a traversable scene: `scene.json`, `display.glb`, and an overlay. Coordinates are meters, Y up, with the plan on the XZ plane. Image y is not flipped: `x = px / px_per_m` and `z = py / px_per_m`.

The committed demo is an asymmetric office. West Annex sits to the left of the corridor, with four more offices, two exits, a scale bar, and a furniture hatch that the wall detector ignores.

## Quickstart

```bash
pip install -r requirements.txt
python -m ingest_blueprint samples/blueprints/office_vector/plan.pdf -o out/office --no-llm
python -m contract.validate_scene out/office/scene.json
```

Viewer, from a second terminal:

```bash
cd viewer && npm install && npm run dev
```

Copy `out/office/display.glb` and `out/office/scene.json` into `viewer/public/office/` before opening the dev server. The page loads those two files, orbits the model, and a click on the floor draws a path to the nearest exit.

`--no-llm` never calls the network. Copy `.env.example` to `.env` only if you want vision labels. The key stays in `.env`.

## Other entry points

```bash
python tools/make_synthetic_plan.py --seed 1 --count 50 --out data/plans
python tools/score_against_truth.py data/plans/seed_001/truth.scene.json data/plans/seed_001/out/scene.json
python tools/path_probe.py out/office/scene.json --room r_L1_001
python -m editor.app
pytest
pytest -m slow
```

DXF files on layers `A-WALL` and `A-DOOR` use the same pipeline. IFC is optional: `pip install -r requirements-ifc.txt`.

A 60-second walkthrough is in [DEMO.md](DEMO.md). What works, and what does not, is in [STATUS.md](STATUS.md).
