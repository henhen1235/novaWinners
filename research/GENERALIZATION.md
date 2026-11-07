# Generalization: Path B on scans we did not design

Date 2026-11-07. Question: is `ingest_lidar` overfit to `tools/make_synthetic_scan.py`
over the Manhattan `tools/layouts.py` plans with 3 m ceilings?

Reproduce (data is gitignored, code is committed):

```
.venv/bin/python -m tools.stress_scans --make data/stress
.venv/bin/python -m tools.ifc_to_scene data/external/ifc_duplex/Duplex_A_20110907.ifc -o data/stress/ifc_duplex
.venv/bin/python -m tools.ifc_to_scene data/external/ifc_clinic/Clinic_Architectural.ifc -o data/stress/ifc_clinic
.venv/bin/python -m tools.stress_scans --score data/stress -o out/stress
```

Cases (all seed 7, 1.5 cm noise and 5% holes unless named otherwise):

- Layouts, built with `tools.stress_scans.build_truth` from arbitrary wall-centerline room
  polygons (one wall rectangle per room edge, so walls can be diagonal or curved):
  `rotated_wing` (corridor block rotated 30 deg plus a two-room wing at 45 deg, doors and an
  exit on the diagonal walls), `l_corridor`, `open_plan` (16 x 11 m, six 0.5 m pillars),
  `curved_wall` (semicircular wall, 16 segments), `closets` (six 1.5 m wide, 0.8 m doors),
  `double_doors` (1.8 m), `wide_corridor` (2.4 m).
- Real geometry: `ifc_duplex` (storey "Level 1": 4 rooms after merging wall-less open-plan
  spaces, 6 doors) and `ifc_clinic` (storey "First Floor": 120 rooms, 141 doors, 11 exits; 5
  rooms with no door path to an exit and 10 ambiguous doors were dropped). Converter:
  `tools/ifc_to_scene.py`. Both use synthetic scans of the IFC footprints at a 3 m ceiling.
  This is real floor-plan geometry, NOT real scanner data.
- Capture, on tiny_office: `ceiling_2_3`, `ceiling_4_0`, `z_up` (file +Z up, no random
  rotation), `centimeters` (Z-up and x100, no flag), `centimeters_scaled` (same file with
  `--scale 0.01`), `point_cloud` (PLY without faces, 1500 points/m^2, Z-up), `cropped_room`
  (east half of office S3 removed), `holes_20` (20% holes), `noise_5cm`.

## Results, before and after the fixes

Door R / P is recall / precision with 0.5 m center matching. "rooms diff / truth" is predicted
minus truth count. The 30-scan benchmark targets are IoU >= 0.90, door R >= 0.85, P >= 0.80,
rooms within +-1.

| case | up err deg | yaw deg | walkable IoU | door R / P | rooms diff / truth | validator errors | runtime s |
|---|---|---|---|---|---|---|---|
| rotated_wing before | 0.00 | -152.2 | 0.956 | 0.90 / 1.00 | -1 / 9 | none | 1.5 |
| rotated_wing after | 0.00 | -152.2 | 0.956 | 0.90 / 1.00 | -1 / 9 | none | 1.6 |
| l_corridor before | 0.00 | -0.0 | 0.931 | 1.00 / 0.88 | +1 / 6 | none | 1.3 |
| l_corridor after | 0.00 | -0.0 | 0.931 | 1.00 / 0.88 | +1 / 6 | none | 1.5 |
| open_plan before | 0.08 | 0.0 | 0.991 | 1.00 / 1.00 | +0 / 2 | none | 1.4 |
| open_plan after | 0.00 | 0.0 | 0.992 | 1.00 / 1.00 | +0 / 2 | none | 1.5 |
| curved_wall before | 0.00 | 180.0 | 0.979 | 1.00 / 1.00 | +0 / 3 | none | 1.2 |
| curved_wall after | 0.00 | 90.0 | 0.979 | 1.00 / 1.00 | +0 / 3 | none | 1.0 |
| closets before | 0.01 | -180.0 | 0.969 | 1.00 / 1.00 | +0 / 8 | none | 1.0 |
| closets after | 0.01 | -180.0 | 0.969 | 1.00 / 1.00 | +0 / 8 | none | 1.0 |
| double_doors before | 0.01 | -180.0 | 0.971 | 1.00 / 1.00 | +0 / 7 | none | 1.3 |
| double_doors after | 0.01 | -180.0 | 0.971 | 1.00 / 1.00 | +0 / 7 | none | 1.3 |
| wide_corridor before | 0.00 | 0.0 | 0.972 | 1.00 / 1.00 | +0 / 7 | none | 1.2 |
| wide_corridor after | 0.00 | 0.0 | 0.972 | 1.00 / 1.00 | +0 / 7 | none | 1.4 |
| ifc_duplex before | 0.00 | 90.0 | 0.979 | 0.67 / 0.57 | +2 / 4 | ROOM_UNREACHABLE | 1.3 |
| ifc_duplex after | 0.00 | 90.0 | 0.979 | 0.67 / 0.57 | +2 / 4 | ROOM_UNREACHABLE | 1.5 |
| ifc_clinic before | 0.00 | 90.0 | 0.965 | 0.78 / 0.81 | +0 / 120 | ROOM_UNREACHABLE | 6.6 |
| ifc_clinic after | 0.00 | 90.0 | 0.965 | 0.78 / 0.81 | +0 / 120 | ROOM_UNREACHABLE | 8.8 |
| ceiling_2_3 before | 0.00 | -180.0 | 0.963 | 1.00 / 0.38 | +12 / 7 | ROOM_UNREACHABLE | 1.4 |
| ceiling_2_3 after | 0.00 | -180.0 | 0.963 | 1.00 / 1.00 | +0 / 7 | none | 1.6 |
| ceiling_4_0 before | 0.02 | -180.0 | 0.971 | 1.00 / 1.00 | +0 / 7 | none | 1.8 |
| ceiling_4_0 after | 0.01 | -180.0 | 0.972 | 1.00 / 1.00 | +0 / 7 | none | 2.2 |
| z_up before | 0.00 | 0.1 | 0.967 | 1.00 / 1.00 | +0 / 7 | NAVMESH_ISLAND | 1.6 |
| z_up after | 0.00 | 0.1 | 0.967 | 1.00 / 1.00 | +0 / 7 | none | 1.8 |
| centimeters before | hung (> 3 min, 1.5 GB and growing), killed | - | - | - | - | - | - |
| centimeters after | stops in 1 s: SCALE_LOW_CONFIDENCE, "pass --scale 0.01" | - | - | - | - | - | - |
| centimeters_scaled before | 0.00 | 0.1 | 0.000 | 0.00 / 0.00 | +0 / 7 | NAVMESH_ISLAND | 1.6 |
| centimeters_scaled after | 0.00 | 0.1 | 0.967 | 1.00 / 1.00 | +0 / 7 | none | 1.8 |
| point_cloud before | crash: ValueError, zero-size array | - | - | - | - | - | - |
| point_cloud after | 0.00 | -0.0 | 0.966 | 1.00 / 1.00 | +0 / 7 | none | 1.4 |
| cropped_room before | 0.01 | 0.1 | 0.920 | 1.00 / 0.80 | +0 / 7 | NAVMESH_ISLAND | 1.6 |
| cropped_room after | 0.01 | 0.1 | 0.920 | 1.00 / 0.80 | +0 / 7 | none | 1.7 |
| holes_20 before | 0.01 | -0.0 | 0.902 | 0.75 / 0.86 | -2 / 7 | none | 1.4 |
| holes_20 after | 0.01 | -0.0 | 0.902 | 0.75 / 0.86 | -2 / 7 | none | 1.5 |
| noise_5cm before | 6.39 | 0.7 | 0.310 | 0.75 / 1.00 | +1 / 7 | ROOM_UNREACHABLE | 1.9 |
| noise_5cm after | 0.82 | 0.7 | 0.875 | 0.88 / 0.88 | +1 / 7 | ROOM_UNREACHABLE | 2.1 |

The yaw column is the scene's yaw relative to the truth (any multiple of 90 deg is fine; the
scorer undoes it). centimeters_scaled IoU 0.000 before was half a pipeline bug (D22: the
recorded input_to_scene left out --scale) and half a wrong score; the scene itself was usable.

30-scan benchmark after the fixes (all targets pass; before in parentheses, from STATUS.md):
IoU 0.958 (0.956) and 0.914 (0.914), door recall/precision 0.996/0.982 (0.979/0.961), rooms
within +-1 100% (96.7%), wall error 0.006 m, obstacle recall 0.997, validator-clean 100%,
max runtime 3.9 s. `pytest -q tests`: 75 passed.

## Root causes and fixes (DECISIONS.md D20-D24)

1. Fixed height bands (D20). The wall test needs points in the 2.0-2.4 m band and a max
   height above 2.15 m. Under a 2.3 m ceiling the ceiling slab itself satisfies both in every
   cell, so every desk and cabinet became a wall. Bands now follow the measured ceiling H
   (upper band top H - 0.12, max-height cap H - 0.1, tall threshold min(2.15, H - 0.15));
   for H >= 2.95 m they are unchanged, which is why the benchmark did not move.
2. Biased axis refinement (D21). `_refine` averaged normals inside a 10 deg cone around the
   current estimate. With 5 cm vertex noise on 0.2 m triangles, normals scatter about
   20 deg, and a truncated cone mean cannot move away from where it started: up stayed
   6.4 deg off, the floor slice cut through the furniture, IoU 0.31. A 25 deg cone (still
   far from the 90 deg axes and the 45 deg wing walls) gives 0.82 deg.
3. Units (D22). Nothing bounded the grid size: a cm file meant a 5 cm grid over 1600 m.
   The pipeline now stops when floor-to-ceiling is outside [1, 10] m without --scale and
   prints a unit hint. --scale is now part of input_to_scene.
4. Navmesh T-junctions (D23). This only showed up on scans aligned exactly with the axes
   (z_up, cropped_room, centimeters_scaled; also 2 of the 30 benchmark scans during
   testing). A walkable ring that runs collinear with itself across a narrow neck makes earcut
   emit triangles that meet only at T-junctions. The validator connects triangles by shared
   vertices, so it reports NAVMESH_ISLAND. The fix is a 1 cm mitre opening plus a 1 mm snap,
   then dropping navmesh components that are not within 1 m of an exit. The drop is reported
   as a warning (at most 0.07 m^2 here).
5. Point clouds (D24). `trimesh.load(force="mesh")` returns an empty mesh for a face-less PLY,
   so the point-cloud branch was never reached. Once that was fixed, the unsigned PCA-normal
   orient path worked on its first run (up error 0.00 deg, IoU 0.966).

Risks we checked that turned out fine: diagonal and curved walls. walls.py emits only
axis-aligned walls, so the 45 deg wing and the curved wall come out as occupied cells, not
walls. Walkable is still right (IoU 0.956 and 0.979). In rotated_wing 9 of 10 doors are
found, including doors on the 45 deg walls (via the exit-opening detector and the watershed
split). I did not check which door was missed. Obstacles at scan edges were not
seen to fail. Closets of 1.5 m, 1.8 m double doors and a 2.4 m corridor all score perfectly.

## What is still unsupported or weak

- Real scanner data. Every case here is still a synthetic scan. The IFC cases test real plan
  geometry only. No Redwood or ISPRS run yet.
- IFC plans: door recall 0.67 (Duplex) and 0.78 (Clinic), and a ROOM_UNREACHABLE validator
  error on both. Not root-caused, for lack of time. The likely cause: the Duplex exterior walls
  are 0.42 m thick but walls.py clamps thickness to 0.35 m, and the converter puts doors on
  merged open-plan rooms. Missed doors leave rooms with no exit path. This is the next thing to
  look at.
- Diagonal walls are not emitted as `walls` (D11 stands). Their wall_centerline error and the
  display's walls_clean are wrong for non-Manhattan parts. Doors in diagonal walls are
  found only as watershed splits or exit openings, so a 0.6-2.2 m diagonal gap between two
  small rooms (< 8 m^2 each, below the split threshold) would be missed.
- Unit detection is a stop with a hint, not auto-scaling. The hint picks the unit from the
  smallest axis extent, which is wrong for a scan whose height exceeds its width.
- holes_20: rooms -2 and door recall 0.75. Big holes at doorways merge rooms. 20% holes is
  beyond the scan guide, but real scans of glass and dark surfaces get there.
- noise_5cm: IoU 0.875, door precision 0.88, ROOM_UNREACHABLE validator error. Noise this
  high blurs the 5 cm grid. Pre-smoothing the mesh (Taubin) would be the next step, not tried.
- l_corridor splits into 2 rooms (+1). This is a watershed split at the bend, within +-1.
- Multi-storey scans, stairs and sloped floors are not tested.
