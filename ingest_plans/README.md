# ingest_plans

Turns a set of CAD-exported floor plan PDFs (one sheet per floor) into one multi-level
`scene.json` in the standard contract, plus `display.glb`, `plan.svg` and a short `report.md`.
It was built on CMU ESIM sheets (Tepper Building, Mudge House) and works on any sheets that keep
their CAD layers.

## Usage

```bash
python -m ingest_plans <floor PDFs or a folder> -o out/<name> [--config NAME|path.json] \
    [--floors A,1,2,3|auto] [--floor-height 4.3] [--no-llm]
```

Examples, with the plans in the gitignored `data/` folder:

```bash
python -m ingest_plans data/cmu/raw/tepper --config tepper -o samples/cmu/tepper
python -m ingest_plans data/cmu/raw/mudge --config mudge -o samples/cmu/mudge
python -m contract.validate_scene samples/cmu/mudge/scene.json
```

The command exits 1 if the contract validator reports errors. `--no-llm` is accepted for parity
with the other pipelines; this reader never calls a model.

## Sheets it reads

File names give the floor and the sheet kind: `<BLDG>-<floor>-ESIM-Base.pdf`,
`<BLDG>-<floor>-ESIM-Type.pdf`, `<BLDG>-<floor>-ESIM-TypeChart.pdf`. Floors are digits or letters.

- Base (required): vector PDF whose paths keep their CAD layer as optional content. Used layers:
  `A-AREA` (room boundaries), `ARCH|A-DOOR` (door symbols), `ARCH|A-FLOR-STRS` (stair treads),
  `A-SPAC-EXTR` (gross outline), `A-SPAC-IDEN-LEDR` (label leaders), and the room number with its
  area label (`2611` over `1,959sf`).
- Type (optional): the same plan hatched by FICM space type. Room types come from it.
- TypeChart (optional): the color key. Without one, the key printed on the Type sheet is used.

## What it does

- Scale per floor: median of sqrt(labeled sf x 0.092903 / traced room area); the spread is in
  report.md. No scale bar is needed.
- Floors are aligned on cores: configured core rooms, else room numbers that repeat floor to floor,
  else stair rooms (found from treads).
- Doors come from door symbols; rooms still cut off get an inferred 0.9 m door on their longest
  wall to a reachable space, listed in `extensions.<key>.inferred_doors`.
- Stairs link stair rooms that overlap on consecutive floors.
- Exits are exterior doors on the grade floors only. A door that opens over a lower floor's
  footprint (roof, terrace) is dropped. Nothing below the lowest floor or above the top is linked.
- Evacuation reachability: doors both ways, stairs downward, basements upward to the ground floor.
  Rooms with no way out are left out of `rooms` and listed in `extensions.<key>.unreachable`.

## Configs

`configs/cmu.json` holds the CMU convention: floors C < B < A < 1 < 2, A is one storey below the
ground floor 1, exits on floor 1. Building files extend it (`"extends": "cmu"`):

- `tepper.json`: floors 1-5, exits on 1 and 2 (the quad entrance), core rooms x901-x914, color
  overrides where the key reuses a color.
- `mudge.json`: floors A-3, 3.4 m storeys, wing-letter room numbers.

Keys: `floors`, `floor_order`, `ground_floor`, `grade_floors`, `floor_height_m`, `ceiling_m`,
`room_number`, `skip_prefixes`, `open_to_below_rgb`, `color_types`, `big_room_retype`,
`stair_suffixes`, `elevator_suffixes`, `align_suffixes`, `name`, `ext_key`, `source_note`.

## Limits

- Needs vector sheets with CAD layers; scanned or flattened PDFs go through the blueprint (WS-A)
  pipeline instead.
- Room types are as good as the color key. Keys reuse colors across codes, and some "Type" sheets
  are colored by department; those floors borrow types from the same room on another floor.
- Door widths come from the symbol extent (0.8-2.0 m); the validator's median-door-width check can
  warn when many wall-less openings are present.
- Floor heights are configured, not read from the sheets.

## Privacy

Source plans are often internal (CMU ESIM needs a login and its terms forbid redistribution).
Keep them under `data/` (gitignored) and commit only generated files. The scene stores derived
geometry, room numbers and FICM codes, never the drawings.

## Tests

`pytest tests/test_ingest_plans.py` builds two synthetic ESIM-style PDFs with PyMuPDF and runs
the CLI end to end, so CI needs no CMU files. The same file checks the committed Tepper and Mudge
scenes (every room reaches an exit; exits at grade on the outline).
