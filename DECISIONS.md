# DECISIONS

Each entry: decision, why, date (2026-11-07 unless noted).

## Contract and validator

- D1. The contract did not exist; WS-B created `contract/` per the shared spec.
  The user said ownership does not matter as long as the JSON shape is unchanged.
- D2. Models use `extra="forbid"`: unknown keys fail schema validation; extra
  data goes under `extensions`. Nullable fields: `doors[].fire_rated`,
  `doors[].opens_toward`, `exits[].assembly_point`, all `assets` fields,
  `warnings[].ref`. List collections default to `[]`; `navmesh`, `walkable`,
  `meta`, `levels` are required. Amended by D16 (assets strings and the
  walls/rooms/doors/exits lists are now required).
- D3. Validator issue codes (severity):
  SCHEMA_INVALID (error, stops further checks), DUPLICATE_ID (error, ids are
  unique across ALL collections), BAD_REFERENCE (error: unknown level / room /
  door id, or a door connecting a room on another level), INVALID_POLYGON
  (error: shapely invalid or area <= 0.01 m^2, also door segment shorter than
  1 cm), EXTENT_OUT_OF_RANGE (error: longest side of the bbox of walls, rooms
  and walkable outside [3, 500] m), DOOR_TOO_NARROW (error: width_m outside
  [0.6, 3.0]), SMALL_ROOM_ARTIFACT (warn: room area < 1 m^2), NO_EXITS
  (error), ROOM_UNREACHABLE (error), ORPHAN_STAIR (warn: stair bottom/top not
  inside a room of its level), NAVMESH_INVALID (error: index out of range,
  triangle_level length mismatch, unknown level, degenerate triangle with XZ
  area < 1e-6 m^2, per-level navmesh area differing from walkable area by more
  than 2%), NAVMESH_ISLAND (error: triangles in a component that cannot reach
  an exit), EXIT_UNREACHABLE (warn: no navmesh triangle within 1.0 m of the
  exit position), SCALE_LOW_CONFIDENCE (warn: median door width outside
  [0.7, 1.3] m).
- D4. Room connectivity graph: nodes are rooms plus "outside"; edges are doors
  (connects pair), stairs (room containing `bottom` on from_level to room
  containing `top` on to_level) and links (same rule on from/to). A point is in
  a room if within 0.5 m of the room polygon on that level. Goal nodes are the
  `connects` entries of every exit's door. Elevators are ignored in v1.
- D5. Navmesh components: triangles sharing a vertex position (rounded to
  1 mm) are connected; components on different levels are joined by links and
  stairs whose endpoints are within 0.5 m of a triangle. A component "touches"
  an exit if a triangle on the exit's level is within 1.0 m of the exit
  position (exits sit on the wall line, the eroded walkable ends about 0.25 m
  inside it).
- D6. Walkable for hand-written/synthetic ground truth: union of room interior
  polygons and door-gap boxes, minus non-passable obstacles and stair
  footprints, eroded by agent_radius_m (0.25 m) with shapely `buffer(-r)`,
  snapped to 1 mm with `shapely.set_precision`. Navmesh = earcut triangulation
  (`trimesh.creation.triangulate_polygon(engine="earcut")`) of exactly those
  polygons, so areas match. Floats in scene.json are rounded to 1 mm.

- D16. One canonical contract at repo root, the union of WS-A's and WS-B's
  required sets (see docs/team/CONTRACT_CHANGE_REQUEST.md). JSON keys, types and nesting
  are unchanged. Now required: `walls`, `rooms`, `doors`, `exits` (no default)
  and `assets` with non-null `display_model`, `floorplan_overlay`,
  `navmesh_debug` (paths relative to scene.json). Still required as before:
  `schema_version`, `meta.units`, `meta.up_axis`, `doors[].is_exit`,
  `doors[].kind`, `stairs[].direction`, `stairs[].is_enclosed`,
  `elevators[].usable_in_fire`, `obstacles[].kind`, `obstacles[].passable`,
  `links[].cost_multiplier`, `links[].bidirectional` (WS-A had defaults for
  these). Why: ingest_lidar (mesh and RoomPlan), ingest_blueprint and both
  fixture builders already emit every one of these fields, so requiring them
  costs nothing and catches truncated scenes. Optional fields stay optional:
  `stairs`, `elevators`, `obstacles`, `points_of_interest`, `links`,
  `warnings`, `extensions` default to `[]`/`{}`; `fire_rated`,
  `opens_toward`, `assembly_point`, `warnings[].ref` stay nullable. Hand-made
  fixtures (tools/layouts.py) now set assets to `display.glb`,
  `debug/overview.png`, `navmesh_debug.glb` like the LiDAR pipeline.
- D17. Validator checks ported from WS-A's validator into
  `contract/validate_scene.py`:
  EXIT_UNREACHABLE (error) when an exit's door connects no room (for example
  `["outside", "outside"]`); the D3 navmesh-distance case stays a warn.
  UNKNOWN_SPACE_NEAR_DOOR (warn) when a door segment is more than 0.3 m from
  the boundary of a room it connects. LEAKY_WALL_SUSPECTED (warn) when, on one
  level, the walkable within 0.30 m of two rooms touches (2 cm tolerance) and
  no door connects that pair. ORPHAN_STAIR (warn) now also covers a link end
  that is not within 0.5 m of a room. Root logic was kept for every other code
  because it is stricter: invalid polygons are reported instead of repaired
  with buffer(0), navmesh area is compared per level, each exit and each
  navmesh island is reported separately. WS-A's code names map as
  POLYGON_INVALID -> INVALID_POLYGON, EXTENT_INVALID -> EXTENT_OUT_OF_RANGE,
  SCHEMA -> SCHEMA_INVALID, "component does not touch an exit" NAVMESH_INVALID
  -> NAVMESH_ISLAND.
- D18. `blueprint/contract/` is deleted; blueprint imports the repo-root
  `contract` package. pytest gets it through `pythonpath = [".", ".."]` in
  blueprint/pyproject.toml, the CLI through a sys.path append in
  `ingest_blueprint/__init__.py`, tools/build_fixtures.py through its own
  sys.path line. `blueprint/tools/__init__.py` was added so `tools.*` in
  blueprint still resolves to blueprint/tools and not the root `tools`
  package. Run the validator from the repo root:
  `python -m contract.validate_scene blueprint/out/office/scene.json`.

## Pipeline

- D7. Rooms come from connected components of interior space with detected
  walls and door segments as barriers, not from watershed. Doors are found as
  gaps along wall lines. Simpler, deterministic, and avoids watershed
  over-segmentation. Revisit if room-count score fails.
- D8. Exit doors are where floor-supported space meets outside-connected
  unknown space over 0.6 to 3.0 m (no wall in between). Human tags
  (exit_tags.json, scene-frame [x, z, label]) add or relabel exits. The
  pipeline is deterministic, so the scene frame is stable between runs.
- D9. Walls must be occupied in the body band (0.2 to 1.8 m) AND have points in
  the upper band (2.0 to 2.4 m): walls reach the ceiling, furniture does not.
  Falls back to body band only when ceiling coverage is poor.
- D10. Enclosed unknown pockets up to 1.0 m^2 (spec suggests 0.3) are filled
  as floor when they do not touch outside-connected unknown and are not
  surrounded by occupied cells (furniture interiors). Reason: unfilled holes
  get eroded by another 0.25 m around them and cost far more walkable area
  than they cover. Tunable.
- D11 (superseded by D29). Path B is Manhattan-first: orient aligns the dominant wall direction
  with X. Diagonal walls still block walkable space (occupied) but are not
  emitted as clean walls.
- D12. Mesh-path doors use kind "opening" (a scan sees the opening, not the
  leaf); RoomPlan doors use "swing".
- D13. LLM: OpenRouter instead of the Anthropic API (that is the key the team
  has). Env: OPENROUTER_API_KEY (from .env), OPENROUTER_MODEL (default
  anthropic/claude-haiku-5.5). Key limit is $7, shared with WS-C.
- D14. Python 3.11 venv at `.venv`; python.org Python lacks CA certs, so HTTP
  calls use `certifi`.
- D15. Levels and stairs modules are folded into orient/export until the
  multi-level stretch goal (spec says one level per scan by default).

## Generalization (stress set, research/GENERALIZATION.md)

- D20. Height bands follow the measured ceiling: upper band =
  (min(2.0, H - 0.55), min(2.4, H - 0.12)), max-height cap min(2.6, H - 0.1),
  wall "tall" threshold min(2.15, H - 0.15). With fixed bands a 2.3 m ceiling
  put the ceiling slab in the upper band of every cell, so all furniture
  became walls (+12 rooms, door precision 0.38). For H >= 2.95 m nothing changes.
- D21. Manhattan-axis refinement averages normals within 25 deg (was 10),
  5 iterations. A 10 deg cone truncates noisy normals around the current
  estimate and cannot move it: 5 cm vertex noise left up 6.4 deg off. 25 deg
  stays far from the 90 deg neighbour axes and the 45 deg diagonal walls.
- D22. No --scale and a floor-to-ceiling outside [1, 10] m: stop with
  SCALE_LOW_CONFIDENCE and a unit hint (mm, cm, inches, feet, from the
  smallest axis extent) instead of rasterizing. A cm file needed a 10^4 x
  larger grid and hung. input_to_scene now includes --scale, so it is the true
  file-to-scene map (scored IoU with --scale 0.01 was 0.0 before).
- D23. Walkable gets a 1 cm mitre opening and is snapped to 1 mm before the
  navmesh, then navmesh components (shared-vertex rule, D5) farther than 1 m
  from every exit are dropped with a warning. Earcut leaves T-junctions where
  a ring runs collinear with itself (common on axis-aligned scans), which the
  validator reads as NAVMESH_ISLAND.
- D24. Face-less PLY files load as point clouds (trimesh force="mesh" returned
  0 vertices and crashed).

## IFC plans (stress set, research/GENERALIZATION.md)

The IFC truth was checked first: every truth door in both plans has no wall
on rays across it and floor under it (exits: on the inside half), and both
truth scenes pass the validator. The failures were pipeline bugs.

- D25. segment_rooms blocks every gap piece up to 2.2 m, not only the door
  pieces. detect_walls bridges the non-door pieces of its first gap pass, but
  the re-snapped walls shift the gaps, so the second pass (in segment_rooms)
  left 1-2 cell slivers between a door strip and the next wall unblocked.
  Rooms leaked through them, both probes of a door landed in the same room and
  the door was dropped: 16 of the 31 doors the Clinic missed (recall 0.78 ->
  0.89).
- D26. Wall runs drop anything at least 1 m long across their own axis
  (ACROSS_MAX_CELLS) before labeling. Double walls (two faces with a void
  between IFC spaces, 0.55-0.65 m after closing) survive the perpendicular
  1 x 11 opening, so one thick vertical wall joined the Duplex's exterior and
  bathroom walls into one component; its bounding box became a 10-11 m
  horizontal wall through both units at the weighted mean row. That made the
  two strip rooms (+2), the mislocated bathroom doors (recall 0.67) and the
  unreachable strip room. No wall is 1 m thick, and thin walls never survive
  the opening, so the benchmark is unchanged.
- D27. Bridges are snap targets but are not snapped themselves, and a non-door
  gap piece is grown into the wall it abuts to at least wall thickness + 1
  cell. Where a perpendicular wall has doors on both sides of a T junction,
  the junction is only a short bridge piece; _snap trimmed it to its
  neighbour's faces (0.2 m long, 0.25 m thick), wall_lines read it back on the
  other axis, and the abutting wall's end looked free. find_gaps then marched
  across the corridor to the next stub: 1.6-1.65 m false doors that split
  corridors (Clinic rooms +11 -> +4, door precision 0.85 -> 0.89).
- D28. After exits are known, a room with no door path to an exit whose
  walkable area reaches a reachable room's walkable area across walkable cells
  outside every room polygon gets a door of kind "opening" on that crossing
  (long axis of the crossing cells, width = crossing + 0.5 m agent diameter,
  clipped to 0.6-2.2 m). Crossings with floor points rank before ones without
  (a wall hole filled as floor by D10), then narrowest first. The walkable
  area is the ground truth of passability; an opening it shows is better than
  a ROOM_UNREACHABLE error. Remaining cause on the Clinic: a door through a
  0.4-0.6 m double wall has reveal faces as deep as the wall; they become
  short perpendicular wall runs, both jamb ends look like T junctions, and
  find_gaps never marches across the door. The fallback recovered 3 such doors
  within 2 cm (1D25, 1B07, 1E27). The fourth was a 1.6 m^2 sliver behind a
  wall found through a tall cabinet; its opening matches no truth door. A
  thickness-aware junction test needs T_MAX above 0.35 m and a correct
  thickness estimate for walls with one weak face; not done.
- D29 (supersedes D11). Walls follow the scan's true shape when it is not
  orthogonal. The wall mask (tall cells, faces <= 0.3 m apart closed, blobs a
  0.6 m disc fits in dropped as furniture, one-face 1-2 cell lines thickened
  to 3 cells) is traced with marching squares on the blurred mask, simplified
  at 3 cm and cut on a 1 m grid into hole-free pieces (ids "w_t..."; the
  contract Wall is a single ring, and 1 m pieces keep a min-rectangle
  centerline within 4 cm of an r 3 m arc). The gate is the length share of
  traced face edges (>= 0.5 m, re-simplified at 5 cm) within 5 deg of X/Z:
  above 0.85 the D11 run detector runs unchanged (bench scans read
  0.95-0.99); below it, straight runs >= 1 m that the mask covers >= 80%
  keep their rectangle and the rest is traced (semicircle scan 0.84, hall
  0.28, round room 0.05). Doors on traced walls come from the wall-mask
  skeleton: endpoints (branches >= 0.3 m, so corner spurs are skipped) facing
  each other within 45 deg, else marching along the end tangent to the next
  wall; 0.6-2.2 m with floor under it and < 20% occupied is a door. They
  become 2-cell barriers in segment_rooms and are dropped within 0.5 m of an
  axis gap door. Stress results (old -> new): non-axis wall centerline error
  round_room 0.16 -> 0.015 m, curved_wall 0.14 -> 0.019 m, rotated_wing
  0.35 -> 0.017 m; door recall round_room 0.5 -> 1.0. Known gaps: orient's
  yaw snap leaves rotated_wing's main block ~3 deg off, so it is traced, not
  boxed; a few skeleton rays on clutter give doors inside one room (dropped,
  since a door needs a room on each side).
