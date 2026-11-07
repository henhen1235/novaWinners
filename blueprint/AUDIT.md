# WS-A floor-plan pipeline audit

Audit and fixes made on 2026-11-07, starting from commit 911ba08. Tests, plans and commands are below so every number can be rerun.

## How it was measured

- `tools/raster_bench.py` runs the pipeline on every `<dir>/plan.png` that has a `truth.scene.json` and prints the scorer's numbers plus validator errors and runtime. A crash counts as a row.
- Bench set (gitignored, rebuilt by the commands below):
  - `syn_01`-`syn_06`: `tools/make_synthetic_plan.py` single-line rasters, rotated plus JPEG.
  - `office_raster`: the committed sample.
  - `curved`: `tools/make_curved_plan.py`, with a round room, a curved corridor, a 30 degree wing and exits on angled and radial walls.
  - `swiss_*`: six real apartment floors from Swiss Dwellings (CC BY 4.0, zenodo 7070952), rendered by `tools/swiss_to_plan.py` at 60 px/m with windows, swing arcs and labels.
- Real CAD sheets: CMU Tepper `TSB-1` and `TSB-3` ESIM base plans (CMU internal, local only, never committed). They have no truth file, so they are scored by the validator and by how many rooms reach an exit.
- Before numbers come from a worktree at 911ba08 running the same bench script.

```bash
cd blueprint
../.venv/bin/python -c "from pathlib import Path; from tools.make_synthetic_plan import write_plan; [write_plan(s, Path('out/bench/syn_%02d' % s), raster=True, distort=True) for s in range(1, 7)]"
../.venv/bin/python -m tools.make_curved_plan --out out/bench/curved
curl -r 0-30000000 -o data/external/swiss/sd_head.csv "https://zenodo.org/records/7070952/files/geometries.csv?download=1"
../.venv/bin/python -m tools.swiss_to_plan data/external/swiss/sd_head.csv --out out/bench --count 6
../.venv/bin/python -m tools.raster_bench out/bench/* --out out/bench_after
```

## Score table

Walkable IoU / room count delta / door recall / exit recall / validator errors. The "before" column is 911ba08.

| plan | before | after |
|---|---|---|
| office_raster | 0.83 / 0 / 0.86 / 0.50 / 2 | 0.93 / 0 / 0.86 / 0.50 / 0 |
| curved | 0.49 / -2 / 0.14 / 0.33 / 0 | 0.83 / -1 / 0.29 / 1.00 / 0 |
| syn_01 | 0.83 / 0 / 0.71 / 0.50 / 2 | 0.93 / 0 / 0.86 / 0.50 / 1 |
| syn_02 | 0.87 / 0 / 0.67 / 0.00 / 9 | 0.94 / 0 / 0.67 / 0.00 / 7 |
| syn_03 | 0.24 / +2 / 0.14 / 0.00 / 0 | 0.70 / +1 / 0.14 / 0.50 / 6 |
| syn_04 | 0.96 / 0 / 0.67 / 0.00 / 1 | 0.95 / 0 / 1.00 / 0.67 / 0 |
| syn_05 | 0.82 / 0 / 0.78 / 0.50 / 5 | 0.93 / 0 / 1.00 / 1.00 / 1 |
| syn_06 | 0.18 / +3 / 0.33 / 0.00 / 1 | 0.68 / +1 / 0.33 / 0.50 / 4 |
| swiss_3135 | 0.03 / -14 / 0.00 / 0.00 / 19 | 0.49 / +2 / 0.17 / 0.00 / 21 |
| swiss_45313 | 0.08 / -8 / 0.00 / 0.00 / 4 | 0.75 / +3 / 0.56 / 0.00 / 2 |
| swiss_43726 | 0.08 / 0 / 0.00 / 0.00 / 15 | 0.43 / -4 / 0.08 / 0.00 / 7 |
| swiss_42197 | 0.05 / -10 / 0.07 / 0.50 / 1 | 0.35 / -2 / 0.07 / 0.50 / 8 |
| swiss_10111 | 0.11 / -3 / 0.00 / 0.00 / 13 | 0.27 / -5 / 0.06 / 0.00 / 5 |
| swiss_13804 | crash (TypeError) | 0.22 / -5 / 0.00 / 0.00 / 12 |

Tepper CAD sheets:

| sheet | before | after |
|---|---|---|
| TSB-3 (offices, 54k paths) | 0 rooms, 0 doors, NO_EXITS, scale 88 px/m (true ~13.7), 28 s | 131 rooms named by number, 117 doors (63 from A-DOOR swings and envelope seams, 54 inferred), 42 exits, 108 of 131 rooms reach an exit, scale from sf tags, 20 s |
| TSB-1 (ground, 82k paths) | 0 rooms, 0 doors, NO_EXITS, 55 s | 123 rooms, 108 doors (104 inferred), 4 exits, 105 of 123 rooms reach an exit, 9 s |

Both Tepper sheets still fail the validator. Most errors are NAVMESH_ISLAND: walkable pieces inside rooms that the 0.25 m agent erosion cuts off.

## What works

- Vector PDFs with real wall strokes, such as `office_vector`, still score 0.999 IoU with every door and exit. All 50 synthetic vector seeds pass (`pytest -m slow`).
- Raster plans with solid walls at about 100 px/m: rooms within one of truth and IoU 0.68-0.95.
- After the fixes: curved and angled walls, glazed envelopes, dense CAD exports, scale from room area tags, room numbers as names, and sheets that draw no doors.

## Failure modes found

Each entry gives an example, the root cause at 911ba08, and the status.

1. Curved and angled walls are lost.
   - Example: `curved` loses the 30 degree wing and the curved corridor, and the whole page is tilted 2 degrees.
   - Cause: `raster_rooms.py` used a square closing kernel and measured door gaps along one image row or column. `_axis_snap` snapped every outline to the axes. `segment.deskew` rotated by the median of all Hough angles, so curves and wings dragged it.
   - Status: fixed.
2. Runtime on CAD exports was 28-55 s.
   - Cause: `scale._dimension` and `_scale_bar` looped over texts times strokes in Python (21 s). `_pair_double_lines` was O(n^2). The raster helpers compared the full label image once per component.
   - Status: fixed. TSB-3 now runs in 20 s and TSB-1 in 9 s.
3. CAD exports produce 0 rooms.
   - Example: TSB-1 and TSB-3.
   - Cause: walls are 0.6 grey hairlines on `A-WALL`, and `_dark()` plus `THICK_WALL_M` drop them. The black hairlines are `A-AREA` room boundaries, which the segment path never closes into faces.
   - Status: fixed by routing dense or layered pages to a wall raster (`cad_raster.py`).
4. Wrong scale on CAD sheets.
   - Example: TSB-3 gave 88 px/m.
   - Cause: `_MILLIMETER` reads room numbers such as `3401` as 3.401 m, and the closest line wins.
   - Status: area tags now set the scale on raster and CAD pages. Dimension text needs two agreeing candidates. On the plain vector path, room numbers can still agree with each other: Tepper gives 39.6 px/m from dimension text alone. See "Remaining failures".
5. Rooms are named by their area tag or come out as "Room".
   - Cause: `rooms._skip_text` drops every all-digit string, which includes room numbers, so "130sf" wins.
   - Status: fixed. Area tags are skipped and numbers such as `3404` or `1001A` lead the name.
6. Sheets that draw no doors.
   - Example: ESIM `A-AREA` boundaries are closed.
   - Cause: no room can reach an exit, which is a design gap.
   - Status: added `geometry/infer_doors.py`. It runs only when doors are sparse or the page has CAD door layers. Each inferred door is flagged and counted in `extensions.inferred_doors`.
7. Circulation is labelled as outside.
   - Example: Tepper corridors and atria.
   - Cause: open stretches of the envelope (glazing, entrances) let corridor free space reach the page edge.
   - Status: fixed with a footprint outline barrier. Long open seams between pieces of one space are merged, and long open seams to the exterior become exits.
8. Windows read as exits, and rooms leak through windows.
   - Example: every Swiss floor.
   - Cause: thin glazing lines were dropped from the wall mask, and the solid vs double-line decision used a fixed 3.5 px median, so 0.2 m walls at 60 px/m counted as "thin".
   - Status: fixed. The decision uses the 90th percentile of stroke width. Thin straight lines that touch a wall join the mask. Thin ink that spans a whole gap marks a window.
9. Navmesh islands next to detected doors.
   - Example: `office_raster` had 2 NAVMESH_ISLAND errors.
   - Cause: raster faces include half of each door gap, and the boundary wall built along that edge sat beyond the door bridge.
   - Status: fixed. Boundary walls skip or cut edges that lie on a door, and wall thickness is measured instead of fixed at 0.2 m.
10. Crashes.
    - `split_gaps` crashed with `'<' not supported between Segment` (Forbes Beeler). Fixed, with a regression test.
    - `detect_raster` failed with `lines[:, 0]` unpack TypeError on swiss_13804. Fixed with `reshape(-1, 4)`.

## Remaining failures

- Swiss plans: door recall is 0.06-0.22. These plans use 0.8-1.0 m openings with no leaf, plus virtual (dashed) area boundaries the truth counts as rooms. Seams through a wide opening are longer than the seal and get merged.
- The 1.2 m link on `curved` is swallowed by the seal, so the hall, link and rotunda merge. Curved door recall fell from 0.57 to 0.29 after thin-line walls were added. I have not traced why.
- Exit recall on the synthetic rasters is 0-0.67. An exit gap opens onto the page margin, and when the closing footprint seals it there is no seam with the exterior label.
- Tepper: 23 of 131 (TSB-3) and 18 of 123 (TSB-1) rooms still do not reach an exit.
  - Some rooms touch only spaces that are not faces.
  - Some inferred doors land on walls that the 0.25 m agent erosion then closes.
  - NAVMESH_ISLAND errors remain inside rooms with narrow necks.
  - Open-to-below areas (`OTB3-*`) are rooms. They should be voids.
- On the plain vector path (fewer than 3000 strokes, no layers), room numbers can still produce a dimension-text scale. Area-tag scale runs only on the raster and CAD branch.
- Exit doors on raster plans can sit tilted across the wall: the room flood and the exterior flood meet off the wall line, so the jamb contacts are uneven. The door still connects; its lintel in display.glb is skewed.
- No OCR. Raster pages without a text layer get no room names and no scale bar reading.

## Methods we can use

Licences were checked on 2026-11-07 against each repo's LICENSE, its model card or the GitHub API. No model was benchmarked on this Mac.

| project | code licence | weights | runs on Mac CPU | verdict |
|---|---|---|---|---|
| DeepFloorplan (zlzeng) | GPL-3.0 | links in repo, terms not stated | no (TF 1.10, CUDA 9) | skip (GPL, TF1) |
| TF2DeepFloorplan (zcemycl) | GPL-3.0 | Google Drive, README says the link is down | plausible (TF2, M1 extra) | skip (GPL, dead weights) |
| CubiCasa5K | CC BY-NC 4.0 | Drive, inherits NC | plausible (PyTorch) | best functional fit (walls, rooms, icons) but non-commercial; eval only |
| Raster-to-Vector (Liu 2017) | MIT | Torch7 weights, provenance unclear; PyTorch port with a free IP solver | Torch7 is CUDA-only | interesting, old, low priority |
| FloorplanToBlender3d | GPL-3.0 | none (classical) | yes | read the ideas, do not copy code |
| RoomFormer, HEAT, PolyRoom | MIT / NC / none | various | CUDA ops | not applicable (scan density input or licence) |
| Roboflow floorplan datasets | per project (floorplan2334 CC BY 4.0, a walls set public domain) | hosted models are API-only | n/a | training data only |
| kiyo07/floorplan-door-detector-rfdetr (HF) | weights Apache-2.0, runner AGPL | safetensors | stated to run on macOS ARM64 | candidate second vote for doors; write our own loader |
| SAM / SAM2 / MobileSAM | Apache-2.0 | Apache-2.0 | ViT-H too slow; MobileSAM fast | poor fit for line drawings |
| rapidocr-onnxruntime | Apache-2.0 | models in wheel (not verified) | yes, no system deps | best OCR to add first |
| easyocr / tesseract | Apache-2.0 | download / brew | yes | fallback OCR |

Adopted today, all classical and with no new dependency:

- Marker watershed for rooms: OpenCV `cv2.watershed`, seeded from a disk dilation of the wall mask.
- Door detection from watershed seams. It works at any wall angle.
- Douglas-Peucker at 2 cm with conditional Manhattan snapping.
- A building footprint by large closing.
- Quarter-arc door swings from CAD layers, reusing `scale._quarter_radius_px`.
- National CAD Standard layer names (`A-WALL`, `A-GLAZ`, `A-AREA`, `A-DOOR`) read from PDF optional content, the same idea the DXF loader already uses.
- Scale from room area tags.

Next, ranked by value for effort:

1. rapidocr for room labels, area tags and dimension strings on raster pages. With OCR the area-tag scale and room names would work on scans too.
2. Use the `A-DOOR` swings for every door on layered sheets, and drop door inference there.
3. Treat `OTB` and "open to below" faces as voids.
4. Optionally test the RF-DETR door detector as a second vote on raster door gaps.

## Changes for Henry

All changes are in `blueprint/`. Nothing in `contract/` or `viewer/` was edited.

- `ingest_blueprint/raster_rooms.py` is mostly rewritten. `extract()` now returns `(scale, faces, doors, wall_m)`. The pipeline is its only caller and has been updated.
- New modules:
  - `ingest_blueprint/cad_raster.py`: dense or layered PDF pages go to the raster path.
  - `geometry/infer_doors.py`: `doors_from_arcs` and `infer_doors`.
- New dataclass fields, all defaulted so existing callers are unaffected:
  - `Stroke.layer`
  - `PlanPage.door_arcs`
  - `DoorOpening.inferred`
- Scale:
  - New `scale.area_label_ratio`.
  - `_dimension` now needs two candidates within 5% of each other when there is more than one.
  - Both text searches are vectorised.
  - The contract has no "area_labels" scale method. The scene says `dimension_text` and sets `extensions.scale_source = "area_labels"`.
- `geometry/rooms.label_faces`: room numbers lead the name, and area tags are never names.
- `pipeline._boundary_walls` takes a thickness and the doors, and cuts walls at doorways. Raster wall thickness is measured, capped at 0.2 m because `assemble_scene` sizes door bridges for 0.2 m.
- `segment.deskew` only rotates when 60% of lines agree within 1 degree and the angle is under 10 degrees.
- `segment.detect_raster` is fixed for the `HoughLinesP` output shape.
- `geometry/vectorize.split_gaps` sorts on numeric fields.
- New tools: `tools/raster_bench.py`, `tools/make_curved_plan.py`, `tools/swiss_to_plan.py`.
- New tests: `tests/test_real_plans.py` (split_gaps tie, area-tag scale, room-number names, door inference, curved plan end to end).
- `DECISIONS.md` still says the raster gate is missed. That is still true for door recall.
