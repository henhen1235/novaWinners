# Rehearsal WS-A plan

Blueprint in, traversable scene out. This workstream stops at `scene.json` and `display.glb`. Crowd simulation is out of scope.

## Milestones

1. M0. Frozen contract, validator, positive fixtures, and one negative fixture per warning code.
2. M1. One synthetic vector PDF runs to a valid scene and a Three.js viewer.
3. M2. Vector path hits the score thresholds on 50 synthetic plans.
4. M3. Classical raster path, plus vision labels with a disk cache and `--no-llm`.
5. M4. Correction editor and navmesh path probe.
6. M5. Stairs, multi-page levels, DXF, optional IFC, sample downloads.
7. M6. Demo artifacts, README, status.

## Coordinate rules

Meters, Y up, plan on XZ. For an image, `x = px / px_per_m` and `z = py / px_per_m` (image y is not negated). PyMuPDF already uses a top-left origin with y down, so pixel conversion does not flip y again.

## Walkable area

`walkable = (union(rooms) - union(walls) - union(obstacles)) ∪ door_bridges`, then `buffer(-0.25 m)`. A door bridge is the door segment buffered flat by `(wall_thickness + 0.05) / 2`.

## Risks

- A gap in a wall centerline merges rooms. Snap, gap closing, and the connectivity check are the guards.
- Scale errors make every later meter wrong. Scale confidence is stored on the scene and surfaced in warnings.
- Raster deskew can shift the origin. Scores compare shapes after centroid alignment. Scale is checked on bounding-box size, separately from origin.
- Double-line corners can leave small gaps. The generator extends those lines so endpoints snap.

## Tests

- `pytest` for the contract, scale math, one vector plan, and five synthetic seeds.
- `pytest -m slow` for all 50 seeds.
- Validator CLI exit 0 on every demo plan.

## How to run

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python -m contract.validate_scene fixtures/tiny_office.scene.json
```
