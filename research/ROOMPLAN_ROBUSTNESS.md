# RoomPlan import robustness

Research for `ingest_lidar/roomplan_to_scene.py`, done 2026-11-07. Five open-source RoomPlan
importers were read, plus Apple's docs and WWDC sessions. The JSON encoding itself is covered in
`samples/roomplan/FORMAT.md` and is not repeated here. Code was read for ideas only. Nothing was
copied, and rumi has no license, so it is read-only for us.

## 1. IBS27/rumi (HackMIT 2026)

- License: none (no LICENSE file, GitHub reports no license). Ideas only, no code reuse.
- Commit read: 1e1e140a73994685a7ffe85cdb5e782daba76c47
- Files: `ios/RumiCapture/ScanModel.swift`, `ios/RumiCapture/SurfaceRecorder.swift`,
  `shared/capture/roomplan.ts`, `docs/room-reconstruction.md`, `convex/roomReconstruction.ts`
  (job plumbing, nothing geometric).

What it does: a web room editor. The iOS app scans one room and writes
`JSONEncoder().encode(capturedRoom)`. It does not use StructureBuilder, so there is no multi-room
path. The app gives RoomPlan 120 s to finish processing and rejects a result with zero walls
("Scan the walls of one room and try again"). A second "detailed" ZIP adds the raw meshes.

Import (`shared/capture/roomplan.ts`):
- Bounded zod schema: file up to 10 MiB. Walls 1 to 200, floors up to 30, doors, windows and
  openings up to 100 each, objects up to 500, polygonCorners up to 512 points.
- Transform check: 16 finite floats, the three axes unit length (tolerance 0.01), mutually
  orthogonal and right-handed (`x cross y . z > 0.99`), bottom row (indices 3, 7, 11) 0 and
  index 15 equal to 1. Column-major, read with three.js `Matrix4.fromArray`.
- Rejects duplicate identifiers across all lists. Rejects any surface with a non-null `curve`
  ("Curved surfaces are not supported yet"). Rejects polygonCorners with 1 or 2 points.
- Floors: `polygonCorners` in local plane coordinates, else the `dimensions` rectangle, mapped
  through the floor transform. Without floors it keeps the walls and does not invent a floor
  boundary ("Exports without floor polygons retain walls without inventing a floor boundary").
- Origin normalization: subtract min x and min z of wall and floor corners, and the lowest floor
  y. Warns when floor corners span more than 0.1 m in y ("uneven or multiple floor elevations").
- Doors and openings: use `parentIdentifier` when it names a wall. Otherwise the opening center
  is transformed into each wall's local frame and accepted when `|z| < 0.08`,
  `|x| + width/2 <= wall_width/2 + 0.08` and `|y| < wall_height/2`, and only if exactly one wall
  matches. If none or several match, the opening is unlinked with a warning. Walls are rendered
  with opening cutouts.
- Not implemented, per its docs: multi-room stitching, curved walls, free-space analysis.

Most useful idea: the parent-wall fallback in the wall's local frame with a uniqueness
requirement, and the rigid-transform check.

## 2. Rod292/3d-home-tv-os, `packages/roomplan-import`

- License: MIT.
- Commit read: 6fca2cda64b6374654b7ea212c7bd5e163126584
- Files: `packages/roomplan-import/FORMAT.md`, `src/index.ts`, `src/parse.ts`, `src/walls.ts`,
  `src/rooms.ts`, `src/openings.ts`, `src/math.ts`, `src/settle.ts`, `src/fixtures.ts`,
  `apps/ios/Appart3DScan/Services/StructureMerger.swift`, `Models/RoomPlanEnvelope.swift`.

What it does: an iOS app scans an apartment room by room and exports an envelope with `rooms`
(each a CapturedRoom), `structure` (CapturedStructure or null) and `roomNames`. The TS package
turns that into a 2D/3D apartment scene.

- Merging on device: `StructureBuilder(options: [.beautifyObjects]).capturedStructure(from:)`.
  If it throws, the app keeps the separate rooms and shows a warning that their alignment may
  be approximate.
- Element choice: use the structure when any of its lists is non-empty, else concatenate the
  rooms. StructureBuilder can return one merged floor for the whole home while each room keeps
  its own floor. When the per-room floors outnumber the structure floors, it uses the per-room
  floors, because they are what delimit rooms.
- Parsing is tolerant. Column-major by default, with row-major auto-detection (translation found
  in indices 3/7/11 while 12 to 14 are zero). Nested `[[c0],[c1],[c2],[c3]]` and
  `{"columns": ...}` matrices are also accepted, as are enum values as a single-key object or a
  plain string. Unreadable elements are skipped with a warning.
- Story: only the lowest story is imported, with a warning counting the skipped elements.
- Walls: segment = center plus or minus (local x projected to xz) times width/2. Walls shorter
  than 1 cm are dropped. Curved walls become their chord, with a warning.
- Duplicate walls (adjacent rooms scanned separately, or a structure keeping both faces): merge
  when the angle difference is under 5 degrees, both ends of the shorter wall are within 0.15 m
  of the longer one's line, and the overlap is at least 50 % of the shorter wall. The longer wall
  is extended to cover both. An alias map redirects openings whose parent was dropped.
- Dominant angle: length-weighted histogram of wall angle mod 90 (0.5 degree bins, +-3 degree
  window), refined by a weighted circular mean (angles times 4) of walls within 6 degrees of the
  peak. The plan is rotated so that direction becomes x.
- Normalization in the rotated frame:
  1. Square walls within 4 degrees of an axis, rotating each about its midpoint.
  2. Align collinear axis walls (same line within 5 cm, gap up to 0.5 m) onto one
     length-weighted line.
  3. Snap corners: union-find over endpoints within 10 cm, or within 30 cm when the two walls'
     line intersection is within 10 cm of both endpoints (angle over 20 degrees).
  4. Project a free endpoint onto another wall's interior when it is within 10 cm (T-junctions).
  5. Drop walls shorter than 5 cm.
- Rooms: floor polygonCorners through the floor transform, with vertices snapped to wall
  endpoints within 0.2 m. Without floors, it traces a closed wall loop (every corner shared by
  exactly two walls), else takes the convex hull of the capture's wall endpoints. Polygons under
  0.25 m2 are dropped. Names come from the user's room name, else the section label whose center
  lies inside the polygon.
- Openings: the parent wall (through the alias map), else the nearest wall within 15 degrees and
  30 cm whose extent covers the center, plus or minus 10 cm. Openings with no wall are dropped
  and counted. Width is clamped to the wall length and the offset clamped inside the wall. A
  door sill under 0.15 m (or any sill under 3 cm) is set to 0.
- Objects: the same category within 30 cm in different captures counts as one object. Floor
  items that sink up to 25 cm into a wall are pushed out (`settle.ts`).
- Frame: meters, y up, no flip. Wall local +z (the normal) points into the room, which it calls
  "likely" from two real scans.

Fixtures: all five `fixtures/*.appart3d.json` are synthetic. `src/fixtures.ts` generates them
in a clean plan frame and then applies a fake AR transform and noise.

Most useful idea: the wall dedupe rule (5 degrees, 0.15 m, 50 % overlap) and preferring per-room
floors over a single merged structure floor.

## 3. Mastershort/neonplan3d, `frontend/src/import/roomplan.ts`

- License: MIT.
- Commit read: 78909dda03a7ca0d35c39cca6a63ac0ba6608ed4
- Files: `frontend/src/import/roomplan.ts`, `roomplan.test.ts`, `builder.ts`, `geom.ts`,
  `frontend/src/geometry/gaps.ts`.

What it does: a Home Assistant floor-plan card that imports many formats (DXF, Sweet Home 3D,
robot-vacuum maps, RoomPlan). RoomPlan is read into a format-neutral `RawPlan`, and a shared
builder makes the rooms.

- Input: a CapturedRoom, or a CapturedStructure's top-level lists. If those are empty, it
  concatenates `rooms[]`.
- Stories: a floor per `story` value. Elevation is the median wall bottom of that story, and
  heights are made relative to story 0 (the scan origin is the phone's height).
- Openings without a door or window category: a sill under 0.15 m makes it a door, otherwise a
  window.
- Rooms: iOS 17 floor polygons, labelled by the section whose center lies inside. Without
  floors, the builder finds rooms from walls:
  - Each wall is extended by `min(0.1 m, 0.2 * length)` at both ends so near-misses meet.
  - `bridgeGaps`: two collinear line ends facing each other 0.5 to 1.3 m apart with nothing in
    between get a bridging segment, treated as a door gap.
  - The bounded faces of the planar graph become candidate rooms.
  - Faces under 1.0 m2 are dropped, as are strips with `2 * area / perimeter < 0.35 m` (the space
    between two wall faces). So is a face that contains other faces making up 40 % or more of its
    area (the outline of the whole house).
- `squareUp`: edges within 4 degrees of an axis are made exact.
- `closeGaps` (`roomsAtInnerFaces: true` for RoomPlan): RoomPlan walls are room-side faces, so
  neighbouring rooms sit a wall's thickness apart. Facing edges of different rooms within
  `clamp(exterior_thickness + 0.08, 0.2, 0.7)` m are moved onto their common center line. The
  median closed gap is reported as the interior wall thickness.
- `align: true`: the plan is rotated by the length-weighted mean of edge angles times 4 (the
  90-degree-periodic mean). An optional user mirror and 90-degree turns exist for odd sources,
  not for RoomPlan.
- Openings attach to the nearest room edge within `max(0.35, 0.75 * exterior + 0.1)` m. An
  opening listed by both rooms of a wall (same edge, offset within 0.2 m, overlapping vertically)
  is kept once.

Fixtures: `roomplan.test.ts` builds surfaces inline with a `surface(len, h, x, y, z, angle)`
helper. They are synthetic.

Most useful idea: `closeGaps`, which turns two room-side faces into one shared wall and gives a
measured interior wall thickness, and the face filters (strip ratio, container face).

## 4. Other converters

### edwardzke/heyday, `backend/apps/scans/floorplan.py`

- License: MIT. Commit eed4bef07106c59fee4bd7c013e944853e24a171.
- A gardening app that renders a single-room SVG plan. The Python code is simple and worth
  reading only as a pitfall. It takes `floors[0].polygonCorners`, keeps `(x, y)` and never applies
  the floor transform, so the outline is in the floor's local frame, not the walls' world frame.
  With no floors it draws a 5 x 5 m square.
- The value of this repo is its data: about 20 real device scans in `backend/media/scans/` and
  `backend/recommendationEngine/Room.json`. Five were added as samples (see "Samples added").

### arthur-leguennec/ha-domotwin, `crates/domotwin-import/src/scan.rs`

- License: MIT. Commit c578bac5af6d8c3fda805fdc84aff8974c135ebd.
- Rust importer for Home Assistant 3D twins. It reads RoomPlan JSON and the parametric USDZ.
- Structure handling: when the top-level lists are non-empty, it uses them. Otherwise it merges
  `rooms[]`. Sections come from the same level as the lists.
- Hard cap of 5 000 elements (`too_large`).
- Stories: the `story` field for JSON. For USDZ, which has none, levels are inferred from floor
  and wall-foot heights that are more than 1.5 m apart.
- Floor level per story: its floors, else the foot of its walls.
- Rooms: the floors' outlines, else the faces the walls make. Before polygonizing,
  `close_corners` moves wall ends that stop short of, or overshoot, another wall's line crossing
  (within 0.5 m, `CORNER_GAP = 50 cm`, skipping near-parallel pairs) onto the intersection.
- The plan is turned so walls run along the axes.
- Its fuzz seeds `scan.json` and `structure.json` are generated by
  `scripts/make-import-fixtures.py`, so they are synthetic.

### huuhuannt1998/vesper_studio, `vesper/lab/scan_roomplan.py`

- License: MIT. Commit 52011e4a0f536118b9223a0dcc873cfc0e7c6ada.
- Python, closest to our use. It reads Polycam / 3D Scanner App / RoomPlan JSON into a plan with
  rooms, doors and an entrance. Named tolerances:
  - `SNAP_M = 0.05`: wall ends this close are one point.
  - `JOIN_M = 0.5`: a wall that stops up to 0.5 m short of another wall is joined to it, with a
    warning.
  - `PROBES_M = (0.1, 0.2, 0.35, 0.5)`: distances across a door's wall at which the rooms on each
    side are looked for.
  - `REACH_M = 0.5`: an opening with no parent stands on the nearest wall this close.
  - `TWICE_M = 0.3`: two doors joining the same rooms this close are one door listed twice.
  - `NEAR_M = 0.3`: an object outside every room goes to the room whose wall is this close.
  - `SLIVER_M2 = 0.05` and `ROOM_LEAST_M2 = 1.5`: faces below these areas are dropped.
- Only the lowest storey among the walls is imported, with a warning. An opening is a door
  without a leaf, and a door to the outside is the entrance. Things the scan does not show
  (ceiling, upstairs) are reported in warnings, never invented.
- This is the same probe-across-the-wall door connectivity our converter already uses
  (`DOOR_PROBE_M = (0.3, 0.6)`). Vesper also probes at 0.1 and 0.2 m, which catches rooms whose
  floor polygon ends right at the wall face.

### Read and rejected

- vedang-123-ai/escape_plan (`backend/app/convert/roomplan.py`, evacuation planning): its LICENSE
  says "All rights reserved", so I did not read it further.
- gbroeckling/padspanHA (`roomplan_import.py`): GPL-3.0, not read.

## 5. Apple RoomPlan limits

Sources: WWDC22 10127 (https://developer.apple.com/videos/play/wwdc2022/10127/), WWDC23 10192
(https://developer.apple.com/videos/play/wwdc2023/10192/), and the RoomPlan documentation pages
(read through `https://developer.apple.com/tutorials/data/documentation/roomplan/<path>.json`
because the HTML renders empty without JavaScript).

- Scene size:
  - One room: "works best for a single residential room with a maximum room size of 30 feet by
    30 feet or around 9 by 9 meters" (WWDC22).
  - Multi-room: "we recommend a maximal total area of 2,000 square feet or around 186 square
    meters", and "works best for single-floor residential houses" with one to four bedrooms
    (WWDC23).
  - Offices and schools are outside the stated target. `exceedSceneSizeLimit` is a runtime error
    of both RoomCaptureSession and StructureBuilder, but no number is published.
- Scanning conditions: at least 50 lux. Avoid single scans over 5 minutes (heat and battery).
  Known problems: full-height mirrors and glass, very dark surfaces, ceilings beyond LiDAR range.
- Multi-room (StructureBuilder, iOS 17+):
  - `capturedStructure(from: [CapturedRoom])` needs every room in one coordinate space: a
    continuous ARSession (`stop(pauseARSession: false)` between rooms) or relocalization against
    a saved `ARWorldMap`.
  - A new session has an incompatible origin and orientation, and StructureBuilder does not
    align rooms itself. It fails with `BuildError.invalidRoomLocation` ("one or more rooms reside
    in a different vicinity"), and a forum report describes rooms "stacked on top of each other"
    after a fresh session.
  - The only option is `beautifyObjects`.
  - WWDC23 says manual stitching leaves "duplicate walls and potentially duplicate objects". The
    top-level arrays are called "merged", but dedupe of shared walls is not stated explicitly,
    and `rooms[]` keeps each room's own walls.
- Story: `story` is on CapturedRoom, Surface and Section. Docs say structures "support rooms
  with varying floor heights and rooms that reside on different floors". How story numbers are
  assigned is not documented. CapturedStructure itself has no `story`.
- Floors and polygons:
  - `Category.floor` and `polygonCorners` are iOS 17. The floor "is represented as a rectangle
    during scanning and is beautified as a polygon when scanning ends".
  - Walls can also carry polygons, for slanted walls or walls with a beam.
  - Corners are "in local plane coordinates". FORMAT.md already verified the axis layout on our
    samples.
- Curved walls: `curve` dates from iOS 16, with `radius`, `startAngle` and `endAngle`; iOS 17 adds
  `center` ("in local coordinates. Corresponds to xz center coordinates"). It applies to walls,
  doors, windows and openings.
- Open plan: `sections` (iOS 17) label areas of one room with a center point. "In a larger room,
  the framework may identify multiple types of areas, for example, a kitchen and a dining room."
  So an open-plan space comes out as one room (one floor polygon) with several section centers,
  not as several rooms. No source says how RoomPlan bounds a space not closed by walls.
- Doors: `door(isOpen: Bool)`. `parentIdentifier` (iOS 17) links doors, windows and openings to
  their wall, and some objects to other objects.
- Confidence (high/medium/low) is about the category, not a geometric error bound.
- No WWDC24 or WWDC25 RoomPlan session was found. Forum threads from June 2025 report no RoomPlan
  changes in iOS 26.

## Samples added

`samples/roomplan/heyday/`: five real CapturedRoom device scans from edwardzke/heyday (MIT),
with coreModel stripped. Details are in its `PROVENANCE.md`. The current converter, run on them
(output to a scratch folder, nothing in the repo changed), gives:

| file | rooms out | doors out | what it shows |
|---|---|---|---|
| `open_plan_living_dining_kitchen.json` | 1 ("livingRoom 1", 54.8 m2) | 7 | the diningRoom and kitchen sections are lost; open plan becomes one room named after the first section |
| `apartment_21_walls.json` | 1 ("unidentified 1", 66.7 m2) | 5 | one scan through several spaces (bed, sofa, fridge); the 21 interior walls do not split the single floor polygon |
| `living_room_7_walls.json` | 1 (30.0 m2) | 2 | baseline |
| `living_room_two_sections.json` | 1 (31.6 m2) | 1 | second section dropped |
| `partial_2_walls.json` | 1 (1.6 m2) | 1 | RoomPlan still writes a 4-corner floor for a 2-wall scan |

## Techniques we adopt

Concrete changes for `ingest_lidar/roomplan_to_scene.py`, in rough priority order. Each can be
written with numpy and shapely.

1. Validate input before geometry (rumi, domotwin):
   - Cap the element count, about 5 000 total.
   - Each transform must be 16 finite floats with orthonormal axes (tolerance 0.01), right-handed
     (`det > 0.99`), and bottom row `t[3] = t[7] = t[11] = 0`, `t[15] = 1`.
   - When `t[12:15]` is zero and `t[3], t[7], t[11]` are not, the matrix is row-major: transpose it
     and warn (Rod).
   - Reject duplicate identifiers within one capture.
   - Fail with one clear error naming the first bad element instead of a numpy traceback.
2. Split open-plan rooms by section. When one floor polygon contains two or more section centers,
   make one sub-room per section. Use shapely `voronoi_diagram` of the section centers, clipped to
   the floor polygon, each cell labelled by its section. Where interior walls cross the polygon,
   cut along them first (step 3) and use the Voronoi split only for parts that still hold more
   than one center. Keep `area_m2` per part. This fixes `heyday/open_plan_*` and
   `living_room_two_sections`.
3. Split a floor polygon by interior walls. Polygonize the floor boundary plus wall lines that lie
   inside it (each extended by `min(0.1, 0.2 * length)`, snapped per step 5). Then keep faces of at
   least 1.5 m2, or of any size if they contain a section center. Merge each smaller face into its
   largest neighbour. This targets `heyday/apartment_21_walls`. Guard it: if splitting gives faces
   under 1.5 m2 that contain no section center, keep the unsplit polygon.
4. Dedupe walls before using them (Rod), for CapturedStructure top level plus rooms[], and for
   folders of rooms: angle under 5 degrees, both ends of the shorter wall within 0.15 m of the
   longer one's line, and overlap at least 50 % of the shorter wall. Keep the longer wall extended
   to the union. Keep an alias map `dropped_id -> kept_id` for `parentIdentifier` lookups.
5. Snap corners before polygonize (Rod, domotwin, vesper):
   - Cluster endpoints within 0.10 m and replace each cluster by the intersection of its walls'
     lines (or their mean when the walls are near-parallel).
   - Extend or trim a free end to another wall's line crossing when the crossing is within 0.5 m
     and the angle between the walls is over 20 degrees.
   - Project a remaining free end onto a wall interior within 0.10 m.
   - Replaces the current blanket 5 cm extension in `_rooms_from_walls`.
6. Filter polygonize faces (neonplan, vesper):
   - Drop faces under 1.0 m2.
   - Drop strips with `2 * area / length < 0.35 m`.
   - Drop a face whose contained faces cover at least 40 % of its area (the house outline).
   - If nothing is left, use the convex hull and emit a `ROOM_FROM_HULL` warning rather than doing
     it silently.
7. Fix the door parent fallback (rumi, Rod). `_snap` today takes the nearest parallel wall by
   perpendicular distance only, so a door can snap to a collinear wall far down the line.
   - Also require the door center to fall within the wall's extent, `-0.1 <= along <= length + 0.1`.
   - Then require exactly one candidate, or the closest one by more than 5 cm.
   - Clamp door width to the wall length.
   - Resolve `parentIdentifier` through the dedupe alias map (step 4).
8. Probe closer to the wall for door sides. Use `DOOR_PROBE_M = (0.1, 0.2, 0.35, 0.6)` (vesper),
   because RoomPlan floor polygons end at the wall's room-side face. Keep the existing dedupe of
   doors that connect the same rooms.
9. Close gaps between adjacent rooms (neonplan `closeGaps`). Facing edges of two room polygons
   that are parallel within 5 degrees, overlap along their length, and sit 0.05 to 0.5 m apart
   move to their common center line. Record the median gap as measured wall thickness, and use it
   instead of `WALL_THICKNESS_M = 0.12` when there are 3 or more samples.
10. CapturedStructure floors (Rod): if `rooms[]` hold more floor polygons than the top-level
    `floors`, use the per-room floors. StructureBuilder can return one merged floor for the
    whole home.
11. Detect rooms from separate sessions (Apple: StructureBuilder needs one coordinate space).
    For a folder of room JSONs, or `rooms[]` without top-level lists, flag rooms in different
    sessions with a `ROOMS_NOT_ALIGNED` warning. Two signals: any two room floor polygons overlap
    by more than 30 % of the smaller one's area, or their wall sets cross each other. Do not try
    to auto-align them. We cannot recover the ARWorldMap, and a wrong guess is worse for
    evacuation routes than a warning.
12. Curved walls: when `curve` is non-null, sample the arc. Use `center` (local xz, iOS 17),
    `radius`, and `startAngle`/`endAngle` (Measurement encoding: check against a real export
    first, since none of our samples has one), every 15 degrees, as a polyline in the wall's
    local frame. Then map it through the transform. With no `center` (iOS 16), use the chord and
    emit a `CURVED_WALL_CHORD` warning (Rod).
13. Scene-size and floor warnings:
    - Warn when one CapturedRoom's floor area is over 81 m2 or a structure's total is over
      186 m2 (Apple's guidance). Offices and schools will often exceed this, and accuracy drops.
    - Warn when floor heights within one story span more than 0.1 m (rumi). Apple's MyHome
      already shows 9 cm of drift.
    - For files without `story`, treat floor or wall-bottom levels more than 1.5 m apart as
      separate stories (domotwin).
14. Objects across rooms (Rod): the same category within 0.3 m in different captures is one
    object.

Not adopted:
- Rotating the plan to the dominant wall angle (Rod, neonplan, domotwin). Exit tags and other
  inputs are in the scan's world frame, so rotation would have to be undone for them. Squaring
  walls within 4 degrees (Rod's step 1) can still be done in place, about each wall's midpoint,
  without rotating the plan.
- Mirroring. Every importer and Apple agree the frame is right-handed and y-up, so no flip is
  needed.

## What was implemented (2026-11-07)

In `ingest_lidar/roomplan_to_scene.py` and the new `ingest_lidar/roomplan_align.py`:

- Input checks: every surface needs a 16-float finite transform and finite dimensions; walls under
  0.1 m, doors/openings under 0.2 m and objects under 1 cm are skipped (`DEGENERATE_SURFACE`).
  A folder ignores non-RoomPlan JSON (scan_notes.json, our own scene.json) and falls back to the
  room_N.json files when structure.json is empty or unreadable (`STRUCTURE_UNUSABLE`).
- Rooms come from each room file: floor polygon(s), else the loop its walls close (wall ends
  extended 15 cm), else the hull (`FLOOR_FROM_WALLS`). Open-plan scans stay one room named after
  all its sections ("livingRoom + diningRoom + kitchen"); the Voronoi split (item 2) was not done.
- Per-room floor height: sills and object heights use the room file's own floor, so 5-10 cm of
  drift between rooms does not turn openings into pass-throughs or float the furniture.
- Same-facing duplicate walls from two room files (within 10 cm, overlapping 80 %) are merged
  (`DUPLICATE_WALLS`). Rod's rule (5 deg, 15 cm, 50 %) also merges the two faces of a thin wall
  in MyHome, so ours requires the same facing side.
- Door copies seen from both rooms are merged even when one copy found no room behind it.
- Doors to unscanned space are classed `pocket` (a scanned room within 2 m behind it, or the door
  sits inside its own floor polygon: closets), `interior` (inside the footprint hull) or
  `exterior`. Exits without exit_tags.json: per connected group of rooms, exterior and interior
  doors; pockets only when a group has nothing else (`EXIT_POCKET_SKIPPED`). Option
  `unscanned_exits=False` (CLI `--unscanned-not-exits`) also drops interior doors once an
  exterior one exists (`UNSCANNED_DOOR_NOT_EXIT`). Every room keeps a path to an exit.
- Curved walls: the arc is sampled in the wall's local x-z plane and kept only when its chord and
  midpoint match the wall's straight extent; otherwise the straight wall is used.
- Objects whose bottom is over 1.0 m above the floor (wall TV, upper cabinets) are passable.
- display.glb cuts each door out of its wall slab and adds a lintel above it.
- Separate sessions: we do auto-align, against the advice above, but only rooms that overlap the
  others by more than 25 % or sit more than 3 m away, and only with at least 2 shared doorways.
  RANSAC over doorway pairs, yaw snapped to nearby walls (length-weighted median), translation by
  weighted least squares over doorways (sigma 15 cm) and shared walls held at the measured median
  wall thickness (sigma 3 cm). Rooms that fail stay put with `ROOM_NOT_ALIGNED`.

Measured on MyHome, moving one room by a random yaw and offset: Bedroom4 8.5 cm / 0.26 deg,
Hallway2 10.8 cm / 0.12 deg, Livingroom 52 cm / 9 deg (wrong). With a single shared doorway the
match was wrong in 4 of 7 rooms, hence the 2-doorway rule. The 5 cm target is not met: the two
copies of one doorway disagree by 5 to 19 cm in Apple's own single-session data.

Not done: input element cap and row-major detection (item 1), Voronoi and interior-wall splits
(items 2, 3), corner snapping (item 5), measured wall thickness for the wall slabs, object dedupe.
