# Tepper Building (CMU building 112), floors 1-5

Generated files only: `scene.json` (contract 1.0, levels L1-L5), `display.glb`, `plan.svg`.
No CMU drawing, raster or PDF is in this folder or anywhere in the repo.

## Source

- CMU Enterprise Space Information Management (ESIM) floor plans for the Tepper Building & Garage,
  floors 1-5: `TSB-<f>-ESIM-Base.pdf` (CAD export with layers A-AREA, ARCH|A-DOOR, A-SPAC-EXTR and
  room number / area labels), `TSB-<f>-ESIM-Type.pdf` (rooms hatched by FICM space type) and
  `TSB-1/2-ESIM-TypeChart.pdf` (color key). Report dates 06/23/2026 and 08/20/2026.
- Downloaded by a team member from CMU's ESIM site, which needs an Andrew ID. CMU copyright; the
  site terms (https://www.cmu.edu/legal/) do not allow redistribution, so the plans stay in the
  gitignored `data/cmu/raw/tepper/` on the team machine and are not shared.
- The scene is derived geometry (room outlines, door positions, room numbers). Floor A, B, C and the
  garage are not included.

## How it was made

`python -m ingest_plans data/cmu/raw/tepper --config tepper -o samples/cmu/tepper`

- Scale per floor: median of sqrt(labeled sf x 0.092903 / traced A-AREA area) over every room of
  50 sf or more. meta.scale_method is "manual"; the real method is recorded as
  extensions.tepper.scale_method = "room_area_labels".

  | Floor | m per PDF pt | IQR | rooms used |
  |-------|-------------|-----|------------|
  | 1 | 0.15663 | 0.15654-0.15675 | 103 |
  | 2 | 0.17356 | 0.17344-0.17372 | 79 |
  | 3 | 0.20320 | 0.20305-0.20332 | 94 |
  | 4 | 0.20320 | 0.20311-0.20340 | 122 |
  | 5 | 0.16931 | 0.16920-0.16939 | 128 |

- Alignment: each sheet is fit to the page, so scale and origin differ per floor. Each floor is
  shifted so its vertical cores (rooms x901-x903 stairs, x911-x914 elevators) land on floor 1's;
  the largest residual is 0.22 m and no rotation was needed.
- Elevations: 4.3 m floor to floor (assumed; OSM has no height or levels for Tepper), ceilings 3.5 m.
- Rooms: A-AREA faces with a room-number label; corridors are the space left inside the gross
  outline after rooms are cut out (strips under 0.9 m are wall). Atrium voids (FICM 070.1) and
  shafts (VPN*) are not floor.
- Types: hatch color under the room (the Type sheet is registered to the Base sheet on the wall
  layer, since it draws the plan at its own scale) mapped to the floor's FICM key; some colors
  carry several codes, so `type` is approximate. Candidates are kept in extensions.tepper.rooms.
- Doors: A-DOOR symbols placed on the wall strip between the two spaces they touch ("outside" when
  the strip faces the exterior); wall-less boundaries between area polygons become "opening" doors.
  Rooms still cut off get an inferred 0.9 m door on their longest wall shared with a reachable
  space (listed in extensions.tepper.inferred_doors). Rooms with no path at all are left out of
  `rooms` and listed in extensions.tepper.unreachable.
- Exits: exterior doors on floors 1 (Forbes Ave grade) and 2 (quad main entrance). Exterior doors on
  3-5 are treated as terraces, not exits.
- Stairs: the x901/x902/x903 stair cores, linked on every pair of consecutive floors they span
  (12 links, cost 2.0). Where a floor's core label did not match its area polygon (2903, 3903,
  4902) the core outline is copied from the nearest floor that has it
  (extensions.tepper.inferred_stair_cores). Elevators x912-x914 are listed with usable_in_fire false.
- Reachability: a room is kept only if it reaches an exit through doors and stairs taken downward,
  and only if the walkable area actually reaches it; wall-less seams between area polygons are
  filled in the walkable so open boundaries do not split floors.

## Known limits

- Door widths come from the door symbol extent clamped to 0.8-2.0 m; openings are up to 3 m wide,
  so the validator's median-door-width scale check warns (median of real doors is about 0.93 m).
- LEAKY_WALL_SUSPECTED and UNKNOWN_SPACE_NEAR_DOOR warnings remain where area polygons meet at
  thin glazing or a door symbol sits off-center.
- WS-A's generic blueprint pipeline finds no rooms on these sheets; see blueprint/FAILURES_CMU.md #3.
