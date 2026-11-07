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
  required sets (see CONTRACT_CHANGE_REQUEST.md). JSON keys, types and nesting
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
- D11. Path B is Manhattan-first: orient aligns the dominant wall direction
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
