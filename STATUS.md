# Status

## What works

- Scene contract (`contract/models.py`, `contract/scene.schema.json`).
- Validator CLI: `python -m contract.validate_scene scene.json`.
- `fixtures/tiny_office.scene.json` (6 offices, 1 corridor, 2 exits) and `fixtures/two_floor_school.scene.json` (2 levels, 2 stairs) pass with no issues.
- Negative fixtures cover every listed warning code. Error codes exit 1. Warning codes exit 0.

## What does not work yet

- Blueprint ingest, raster path, editor, and viewer are not implemented.

## How to run

```bash
source .venv/bin/activate
python -m contract.validate_scene fixtures/tiny_office.scene.json
pytest
```

The virtualenv was created with Python 3.11.17 (`uv python install 3.11`).

## Known issues

- None in the validator fixtures.
