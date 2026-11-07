# Contract change request: merge WS-A and WS-B contracts (schema_version 1.0)

Date: 2026-11-07. Status: applied. Decisions: DECISIONS.md D16 to D18.

## Why

WS-A (`blueprint/contract/`) and WS-B (`contract/`) each wrote the scene
contract from the same spec. The JSON keys, types and nesting were identical;
only the required and default sets differed, and the validators reported
different codes. Two copies would drift, so there is now one contract at
`contract/`, used by ingest_lidar, ingest_blueprint, the viewer and the tools.

The JSON shape does not change. `schema_version` stays "1.0".

## Required and optional fields

Rule: a field is required when every producer always emits it. Producers
checked: `ingest_lidar` (mesh path and RoomPlan path), `ingest_blueprint`
(`export/assemble.py`), `tools/layouts.py` and `blueprint/tools/build_fixtures.py`.

| Field | WS-B before | WS-A before | Now |
|---|---|---|---|
| `walls`, `rooms`, `doors`, `exits` | default `[]` | required | required |
| `assets` | default all null | required | required |
| `assets.display_model`, `floorplan_overlay`, `navmesh_debug` | nullable | required string | required string |
| `schema_version`, `meta.units`, `meta.up_axis` | required | default | required |
| `doors[].is_exit`, `doors[].kind` | required | default | required |
| `stairs[].direction`, `stairs[].is_enclosed` | required | default | required |
| `elevators[].usable_in_fire` | required | default | required |
| `obstacles[].kind`, `obstacles[].passable` | required | default | required |
| `links[].cost_multiplier`, `links[].bidirectional` | required | default | required |
| `stairs`, `elevators`, `obstacles`, `points_of_interest`, `links`, `warnings`, `extensions` | default | default | default (unchanged) |
| `fire_rated`, `opens_toward`, `assembly_point`, `warnings[].ref` | nullable | nullable | nullable (unchanged) |

WS-B's value constraints (ring length >= 3, 2D/3D point lengths, positive
heights and widths, `scale_confidence` in [0, 1]) are kept. WS-A had none, and
every WS-A output and fixture already satisfies them.

The only change that can reject a scene that passed before is `assets`: a
scene with null asset paths now fails SCHEMA_INVALID. The hand-written
fixtures were regenerated with `display.glb`, `debug/overview.png` and
`navmesh_debug.glb`. Synthetic truth scenes under `data/synth/` that were
generated before this change still have nulls; regenerate them with
`python -m tools.make_synthetic_scan --bench data/synth`.

## Validator

New in `contract/validate_scene.py`, ported from WS-A:

- EXIT_UNREACHABLE (error): the exit's door connects no room.
- UNKNOWN_SPACE_NEAR_DOOR (warn): door segment more than 0.3 m from a room it connects.
- LEAKY_WALL_SUSPECTED (warn): walkable joins two rooms that share no door.
- ORPHAN_STAIR (warn) now also flags link ends that are not inside a room.

WS-A code names map to WS-B names: POLYGON_INVALID -> INVALID_POLYGON,
EXTENT_INVALID -> EXTENT_OUT_OF_RANGE, SCHEMA -> SCHEMA_INVALID, and the
"component does not touch an exit" NAVMESH_INVALID -> NAVMESH_ISLAND.

CLI: `python -m contract.validate_scene scene.json [--json]`, run from the repo
root. `--json` prints `{ok, errors, warnings, issues}`. Exit status is 0 only
when there is no error.

## Regenerating

    python -m contract.models   # writes contract/scene.schema.json
    python -m tools.layouts     # writes fixtures/*.scene.json and the error-code negatives

The warn-code and EXIT_UNREACHABLE negatives in `fixtures/negative/` are copies
of `blueprint/fixtures/negative/*` (built by `blueprint/tools/build_fixtures.py`).
