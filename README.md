# Rehearsal (WS-A)

Turn a 2D floor plan into a traversable 3D scene (`scene.json` + `display.glb`).

## Quickstart

```bash
pip install -r requirements.txt
python -m contract.validate_scene fixtures/tiny_office.scene.json
pytest
```

Blueprint ingest lands in the next milestone:

```bash
python -m ingest_blueprint samples/blueprints/office_vector/plan.pdf -o out/office --no-llm
python -m contract.validate_scene out/office/scene.json
```

Copy `.env.example` to `.env` if you want vision labels. `--no-llm` does not call the network.
