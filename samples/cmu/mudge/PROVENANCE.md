# Mudge House (CMU building 050), floors A, 1, 2, 3

Generated files only: `scene.json` (contract 1.0, levels LA, L1, L2, L3), `display.glb`,
`plan.svg`, `report.md`. No CMU drawing, raster or PDF is in this folder or anywhere in the repo.

## Source

- CMU Enterprise Space Information Management (ESIM) plans for Mudge House: `MGH-<f>-ESIM-Base.pdf`
  (CAD export: A-AREA room boundaries, ARCH|A-DOOR, ARCH|A-FLOR-STRS stair treads, room number and
  area labels) and `MGH-<f>-ESIM-Type.pdf` (rooms hatched by FICM space type, key on the sheet).
  Report dates 06-01-2026 and 08/21/2026.
- Downloaded by a team member from CMU's ESIM site (Andrew ID login). CMU copyright; not
  redistributable (https://www.cmu.edu/legal/), so the plans stay in the gitignored
  `data/cmu/raw/mudge/` on the team machine.

## How it was made

`python -m ingest_plans data/cmu/raw/mudge --config mudge -o samples/cmu/mudge`

- Scale per floor from room area labels (median of sqrt(sf x 0.092903 / traced area)):
  A 0.10583 m/pt (IQR 0.10577-0.10591, 66 rooms), 1 0.10155 (0.10147-0.10161, 59),
  2 0.10159 (0.10150-0.10163, 73), 3 0.09737 (0.09727-0.09743, 62).
- Alignment: rooms that repeat floor to floor (same number without the floor digit, area within
  15%) on floors 1-3; floor A numbers its rooms differently (C19, B12), so it is aligned on its
  stair rooms (found from the drawn treads). Shifts are in report.md.
- Levels: CMU convention, A is one storey below ground floor 1. 3.4 m floor to floor (assumed),
  2.8 m ceilings. LA is at -3.4 m.
- Exits: exterior doors on floor 1 only (17). Floor A has 4 exterior door symbols (wing ends and the
  south side); it is a basement by the CMU convention, so they are not used as exits and A residents
  climb to floor 1. Set `"grade_floors": ["A", "1"]` in the config if those doors are at grade.
- Stairs: 17 links where stair rooms overlap on consecutive floors (A-1: 5, 1-2: 6, 2-3: 6).
- Types: FICM codes from each floor's own key. 920 (sleep/study with bath) rooms stay contract type
  "unknown" (no bedroom type); the code is in extensions.mudge.rooms. Floor 3's Type sheet is
  colored by department, so floor 3 rooms take the code of the same room on floor 2.
- Doors: 594 from door symbols, 29 wall-less openings, 36 inferred (extensions.mudge.inferred_doors).
  6 rooms (A205, A207 suites on floor 2) have no path out and are left out.

## Validator

0 errors; warnings: LEAKY_WALL_SUSPECTED, UNKNOWN_SPACE_NEAR_DOOR, 3 SMALL_ROOM_ARTIFACT.
