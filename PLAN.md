# PLAN: WS-B, LiDAR scan to traversable scene

Scope: scene building from a LiDAR scan only. Output per scan: `scene.json`
(frozen contract in `contract/`) plus `display.glb`, `navmesh_debug.glb` and a
debug PNG set. Simulation, LLM occupants, adversary and optimizer are WS-C.

## Milestones (in order; each ends with green tests, a commit, STATUS.md)

| M | Deliverable | Done when |
|---|-------------|-----------|
| M0 | `contract/` (pydantic models, generated JSON Schema, validator), fixtures `tiny_office`, `two_floor_school`, one negative fixture per validator error code, repo skeleton | validator exits 0 on both fixtures and non-zero with the expected code on every negative |
| M1 | Vertical slice: `tools/make_synthetic_scan.py` -> `python -m ingest_lidar scan.obj -o out/x` -> load, orient, occupancy, walkable, exit openings, navmesh, valid `scene.json` + `display.glb` | a synthetic scan of `tiny_office` validates with exit code 0 |
| M2 | walls, rooms, interior doors, obstacles; `tools/score_against_truth.py`; 30 seeded synthetic scans | targets in section "Scoring" met on the benchmark |
| M3 | (parallel, subagent) `ios/RoomScanner` Swift app + `ios/README.md`; `ingest_lidar/roomplan_to_scene.py` tested on a sample CapturedRoom/CapturedStructure JSON | converter output validates |
| M4 | first real one-room scan end to end; `tools/tag_exits.py` click-to-tag flow; `--corrections` | real scan validates |
| M5 | full-floor real scan; performance (<= 90 s on 500k triangles); stairs (stretch) | |
| M6 | hardening: hole handling, warning quality, README, SCAN_GUIDE, demo script | |

## Pipeline (Path B, generic mesh)

`load -> orient -> occupancy -> walkable -> walls -> rooms/doors -> obstacles -> exits -> navmesh/export -> display`

- orient: Manhattan frame from area-weighted face normals; the up axis is
  scored by (a) two dominant extreme planes, (b) furniture-top asymmetry
  (horizontal faces between floor and ceiling face up, never down), (c)
  floor-to-ceiling extent in [2.0, 4.8] m, (d) a small prior for the file's own
  Y. Output is a proper rotation (det +1), recorded in
  `extensions.lidar.input_to_scene` so scoring can undo it.
- occupancy: area-weighted surface samples, 5 cm XZ grid, heights relative to a
  per-2 m-tile floor estimate (handles floor drift). Bands: floor, body, upper wall.
- walkable: floor_supported and not occupied; enclosed small unknown pockets
  are filled; outside-connected unknown is never walkable; erode by 0.25 m.
- walls: cells that are occupied in the body band AND reach the upper band
  (walls touch the ceiling, furniture does not), kept only if part of a long
  axis-aligned run (morphological opening with a line element).
- doors: gaps along a wall line of 0.6 to 2.2 m that are free at body height.
  Exit doors: places where floor meets outside-connected unknown over 0.6 to 3 m.
- rooms: connected components of interior space with walls and door segments
  as barriers.
- obstacles: non-wall body-band blobs.

## AI usage (OpenRouter)

Semantics only, never coordinates. Cached on disk by SHA-256 of (model,
prompt, image bytes). Every stage runs with `--no-llm`. Uses: room type and
name from geometry descriptors, exit sign text from photos listed in
`scan_notes.json`. Key: `OPENROUTER_API_KEY` from `.env`; model from
`OPENROUTER_MODEL` (default `anthropic/claude-haiku-5.5`). The key has a $7
limit shared with WS-C, whose LLM occupants need most of it.

Development also uses AI agents: parallel subagents for the iOS app, the
RoomPlan converter and method research.

## Risks

1. Up-axis ambiguity on box-shaped single rooms. Mitigation: furniture
   asymmetry cue, prior for the file's Y, `--up` override, scores in debug output.
2. Scan holes at doors disconnect rooms. Mitigation: fill enclosed unknown
   pockets, UNKNOWN_SPACE_NEAR_DOOR warning, `--corrections` add-door.
3. Validator requires every navmesh triangle to reach an exit. Mitigation:
   trim exit-unreachable walkable pockets with a warning.
4. No LiDAR device available to the agent. Mitigation: synthetic scans first;
   real scans need a human (SCAN_GUIDE.md, ios/README.md).
5. Small LLM budget. Mitigation: cache, cheap default model, `--no-llm` path.

## Test plan

- `tests/test_contract.py`: schema matches models; fixtures pass; each negative
  fixture fails with its code.
- Unit tests per module on small synthetic meshes (box rooms, L-shaped room to
  prove no mirroring, random rotations for orient).
- `@pytest.mark.slow`: synthetic scan end to end, determinism (byte-identical
  scene.json except `generated_at`).
- Benchmark (`python -m tools.score_against_truth`): 30 seeded scans, report
  vs targets below.

## Synthetic scan generator spec (tools/make_synthetic_scan.py)

Input: a ground-truth scene (single level) from `tools.layouts`. Steps, all
with `numpy.random.default_rng(seed)`:
1. Add furniture to the truth scene: per non-corridor room 1-3 boxes (desk
   1.2-1.6 x 0.6-0.8 x 0.75; cabinet 0.8-1.2 x 0.4-0.5 x 1.8-2.0, often against
   a wall; table 1.0-2.0 x 0.8-1.0 x 0.75), keep 1.0 m clear in front of doors,
   plus one 0.4 x 0.4 pillar to the ceiling in the largest room. Reject a
   placement if walkable stops reaching every room. Recompute truth walkable
   and navmesh. Write `truth.scene.json`.
2. Mesh in the truth frame (y up), consistent outward winding:
   floor = rooms + door gaps minus furniture footprints (normal +Y); ceiling at
   H (normal -Y); wall prisms without top/bottom caps; lintels over doors from
   2.1 m to H; furniture boxes without bottom faces. Subdivide to max edge 0.2 m.
3. Damage: holes covering `hole_frac` of surface area (blobs r 0.1-0.5 m,
   centers 40% near furniture, 30% near room corners, 30% uniform), 2-4 missing
   ceiling patches, 3 small phantom patches 1-3 m outside exterior walls
   ("glass" reflections), optional floor drift (linear, up to 4 cm across the
   building, applied to all vertices), Gaussian vertex noise sigma.
4. Random rotation (scipy Rotation.random) about all axes plus translation up
   to 10 m; store the 4x4 truth->mesh matrix in `truth_transform.json`.
5. Write `scan.obj` and `scan.ply`. Benchmark: 30 scans, 20 at (sigma 1.5 cm,
   5% holes), 10 at (3 cm, 10%), from `corridor_office(seed)` and the fixtures.

Scoring (tools/score_against_truth.py): the pipeline records its own
input->scene 4x4 in `extensions.lidar.input_to_scene`; composite
M = input_to_scene @ truth_to_mesh maps truth to scene. Up error = angle
between M applied to +Y and +Y. Compare 2D geometry after undoing the yaw and
translation of M: walkable IoU; doors matched by center distance <= 0.5 m
(recall/precision); room count; wall centerline error = median distance from
points every 0.1 m on truth wall centerlines to the nearest predicted wall
centerline; obstacle recall for truth obstacles > 0.5 m tall (matched if a
predicted obstacle intersects the footprint); validator result; runtime.

## Path B algorithm details (ingest_lidar)

- load.py: trimesh.load(force="mesh"); USDZ via usd-core (UsdGeom meshes, world
  transforms, stage up axis and metersPerUnit). Point clouds allowed (normals
  via PCA on k=12 neighbours, unsigned). Report bbox, triangle count, colors.
- orient.py: Manhattan frame: candidate axes = 1000 area-weighted face
  normals, support = area within 10 deg (|n.a|) over a 50k sample, a1 = best,
  refine by mean of aligned normals; a2 = best candidate perpendicular to a1;
  a3 = a1 x a2. Per axis a: offsets of aligned faces (|n.a| > 0.95) in 5 cm
  bins; lo/hi = first/last bin with mass >= 10% of max; planar = mass within
  0.1 m of lo or hi / aligned mass; mid = aligned faces with offset in
  (lo + 0.2, hi - 0.2); asym = sum(area * sign(n.a)) / sum(area) over mid
  (furniture tops face up, nothing faces down); ext = hi - lo.
  score = planar + |asym| * min(1, mid_area / (0.005 * total_area))
  + 0.5 * [2.0 <= ext <= 4.8] + 0.3 * (1 - ext / max_ext) + 0.2 * |a . Y_in|.
  Up = argmax; sign from asym when |asym| >= 0.3, else the side with more
  clutter within 0.15-1.2 m of the plane is the floor. Rotation rows
  [e_x, up, e_x x up] (det +1), e_x = the horizontal axis with larger extent.
  Translate floor to y = 0 and robust min x,z to 0. Ceiling height = hi - lo;
  outside [2.2, 4.5] m -> SCALE_LOW_CONFIDENCE unless --scale given.
- occupancy.py: sample max(2M, 4000/m^2) points (cap 6M) area-weighted, fixed
  seed. Grid 5 cm, row = z, col = x. Local floor per 2 m tile = mode of y of
  points within 0.25 m of y = 0, median-filled, bilinear to cells. Counts per
  cell in bands relative to the local floor: floor [-0.1, 0.1], body
  [0.2, 1.8], upper [2.0, 2.4]; max height per cell below 2.6 m.
- walkable.py: occupied = body >= 2 points; floor = floor band >= 1, closed
  3x3; furniture pockets (enclosed non-occupied components < 3 m^2 with < 20%
  floor) become occupied; enclosed unknown pockets <= 1 m^2 not connected to
  the grid border become floor; free = floor & ~occupied; drop specks
  < 0.5 m^2; distance transform, keep cells farther than 0.25 m from any
  non-free cell; vectorize (contours, buffer +res/2 mitre, simplify 0.03 m).
- walls.py: tall = body >= 2 and upper >= 1. Close along each axis with a
  0.3 m line element, open with a 0.5 m line element; connected components of
  each direction = wall segments (centerline = mean row/col, thickness clamped
  [0.1, 0.35] m); snap ends within 0.3 m to perpendicular walls.
- rooms.py: doors: from each wall end march along the wall line up to 2.4 m to
  the next tall cell; gap 0.6-2.2 m that is < 20% occupied in the body band ->
  door candidate (dedupe). Interior space = (floor | non-wall occupied) minus
  rasterized walls; barriers = walls + door segments; 4-connected components
  >= 1 m^2 are rooms (smaller merge into the neighbour). connects = room label
  probed 0.3 m beyond each wall face ("outside" if none); drop doors whose
  two sides are the same room or both outside. Room type "corridor" when the
  min rotated rectangle has aspect >= 3 and width <= 2.5 m, else "unknown".
- exits.py: exit openings = floor cells 4-adjacent to border-connected
  unknown, clustered; cluster extent 0.6-3.0 m -> exit door (connects
  [room, "outside"]), snapped onto a parallel wall line within 0.3 m; wider ->
  LEAKY_WALL_SUSPECTED. Then exit_tags.json snaps tags within 1.5 m to doors.
- obstacles.py: body-occupied minus rasterized walls dilated 0.1 m,
  components, holes filled, area >= 0.04 m^2; height = max cell height; kind
  pillar if height >= 2.2 and area <= 0.6, furniture if height < 2.2, else
  unknown.
- export.py: trim walkable parts that cannot reach an exit (warn), navmesh via
  geometry.navmesh_from_walkable, UNKNOWN_SPACE_NEAR_DOOR when > 15% of the
  cells within 0.5 m of a door are unknown, run contract validator, write
  scene.json, validation.json, debug PNGs (occupancy, floor_supported,
  occupied, unknown, walkable, rooms, doors, obstacles) and scene.geojson.
- display.py: decimate to <= 500k faces (fast_simplification), drop faces
  above 2.5 m, keep vertex colors (texture -> vertex colors first); node
  "scan" plus node group "walls_clean" (extruded wall polygons).

## Scoring targets

Up axis error < 2 deg on 100%; walkable IoU >= 0.90 (sigma 1.5 cm, 5% holes)
and >= 0.80 (3 cm, 10%); door recall >= 0.85, precision >= 0.80; room count
within +-1 on >= 85%; wall centerline median error < 0.10 m; obstacle recall
>= 0.80 for objects > 0.5 m tall; 100% rooms reach an exit; <= 90 s for 500k
triangles; deterministic.
