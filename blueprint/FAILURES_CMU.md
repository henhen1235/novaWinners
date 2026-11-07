# WS-A failures on real CMU plans

Real CMU floor plans run through `python -m ingest_blueprint ... --no-llm`, logged for the
WS-A improvement work. Plans are CMU copyright ("no reproduce ... or distribute without
written permission", https://www.cmu.edu/legal/index.html), so they are not copied into the
repo. Download them with the URLs below into `data/cmu/raw/` (gitignored). Overlays referenced
here live under `data/cmu/out/` on the shared machine.

Run from `blueprint/`:

```bash
../.venv/bin/python -m ingest_blueprint ../data/cmu/raw/<file>.pdf -o ../data/cmu/out/<name> --no-llm
(cd .. && .venv/bin/python -m contract.validate_scene data/cmu/out/<name>/scene.json)
```

## 1. Forbes Beeler dormitory, 1st floor: crash in split_gaps

- Plan: https://www.cmu.edu/housing/our-communities/residences/floor-plans/forbes-beeler/forbes-beeler-floor-1.pdf
  (local `data/cmu/raw/forbes-beeler-floor-1.pdf`). CAD export, 1224 x 792 pt, 14084 strokes, all hairline
  (width 0), black. No scale bar on the sheet.
- Result at 57c6514: `ingest failed: '<' not supported between instances of 'Segment' and 'Segment'`.
- Cause: `geometry/vectorize.py` `split_gaps` builds `(start, end, thickness, Segment)` tuples and calls
  `intervals.sort()`. Two identical intervals fall through to comparing `Segment`. Fix:
  `intervals.sort(key=lambda item: item[:3])`. I tried that locally (then reverted, your file): the run
  completes, so the items below are what is left after the crash fix.
- With the crash fixed: 1 room (0.06 m^2), 1 door, 236 walls; validator `OK (0 errors, 2 warnings)`
  (SMALL_ROOM_ARTIFACT, EXIT_UNREACHABLE "navmesh has no triangles"). Overlay: `data/cmu/out/forbes-beeler-floor-1/overlay.png`.
- Scale: `scale_bar` at confidence 0.95 with px_per_m = 32.16, but the sheet has no scale bar, so that is a
  false positive. The real scale is much larger (rooms read about 3x too small).
- Page has `/Rotate 270` (mediabox 792 x 1224, rect 1224 x 792). The raster is 3400 x 2200 (landscape) but
  `TextSpan` bboxes come out in the unrotated frame (x up to ~1200, y up to ~3100), so room labels land in
  the wrong place. Strokes look right in the overlay, so only text needs `page.rotation_matrix`.
- Walls are double hairlines; with the wrong scale the 0.10 to 0.40 m pairing window misses them.

## 2. Cohon University Center, 1st floor: wayfinding map, no rooms

- Plan: https://www.cmu.edu/cohon-university-center/images/floor-plans/cohon-center-building-map-1st-floor.pdf
  (local `data/cmu/raw/cohon-center-building-map-1st-floor.pdf`). Vector, 400 x 629 pt, 338 drawings,
  110 words. Rooms are colored filled polygons with no stroked walls; doors are small filled triangles;
  stairs are a zigzag glyph; "E" marks elevators. No scale bar.
- Result at 57c6514: 0 rooms, 0 doors, 30 walls; scale `door_reference` 0.55.
  Validator: `error NO_EXITS [-] scene has no exits` -> FAIL (1 error).
  Overlay: `data/cmu/out/cohon-center-building-map-1st-floor/overlay.png`.
- What would make it work: treat filled, non-white, non-gray-legend polygons as room faces (edge = wall),
  filled triangles as door markers on the nearest face edge, and the face union boundary as the exterior.

## 3. Tepper Building (TSB), floors 1-5: ESIM CAD base plans, 0 rooms on every floor

- Plans: `data/cmu/raw/tepper/TSB-{1,2,3,4,5}-ESIM-Base.pdf` (CMU ESIM, behind CMU login, CMU copyright:
  local only, never commit). Vector CAD export, 1224 x 792 pt, unrotated, 16k-82k drawings. Every path
  carries its CAD layer through PDF optional content (`page.get_drawings()[i]["layer"]`):
  `ARCH|A-WALL` (5325 paths on L2), `ARCH|A-DOOR` (1240), `ARCH|A-GLAZ` (4644), `ARCH|A-FLOR-STRS` (456),
  `A-AREA` (243, closed room boundaries), `A-SPAC-IDEN-RMNO` (room number + area text, e.g. `2611` /
  `1,959sf`), `ARCH|S-COLS`, `ARCH|P-FIXT`, `ARCH|A-EQPM`, furniture, handrails.
- Command: `../.venv/bin/python -m ingest_blueprint ../data/cmu/raw/tepper/TSB-2-ESIM-Base.pdf -o ../data/cmu/out/tepper_raw_2 --no-llm`
  (runtime 5 s for A, 61 s for L1).
- Result: 0 rooms, 0 doors on all five floors. Validator: `error NO_EXITS [-] scene has no exits` -> FAIL.
  Overlays: `data/cmu/out/tepper_raw_{1..5}/overlay.png`.
- Scale: WS-A says `dimension_text` 0.8 with px_per_m = 88.3 on L2. True value is 16.0 px/m
  (0.17355 m per PDF point; median over 94 rooms of sqrt(labeled sf * 0.092903 / traced A-AREA area),
  IQR 0.17334-0.17377). It probably parsed an area label like `1,177sf` as a dimension. 16.0 px/m is
  just above `_plausible`'s 15 px/m floor, so small-scale sheets like this are near the edge.
- Walls are all hairline (width 0), color 0.6 gray on A-WALL, so `_dark()` and `THICK_WALL_M` drop them.
- What would make it work: read the PDF OCG layer names like the DXF path does (A-WALL, A-DOOR), use
  A-AREA polygons as room faces when present, and take scale from `<n>sf` labels inside A-AREA faces.
  Many walls are curved (atrium on L2-L5, auditorium fronts), drawn as bezier `c` items.
