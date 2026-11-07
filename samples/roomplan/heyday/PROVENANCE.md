# Provenance: heyday

Five real RoomPlan `CapturedRoom` exports (`JSONEncoder().encode(capturedRoom)` on a device)
from the Heyday gardening app, uploaded by its iOS client and committed to the repo.

- Source repo: https://github.com/edwardzke/heyday
- Pinned commit: eed4bef07106c59fee4bd7c013e944853e24a171
- License: MIT, Copyright (c) 2025 Edward Ke (copy in `LICENSE`)
- Downloaded: 2026-11-07

| file here | source path at that commit | walls | doors | windows | openings | floors (corners) | objects | sections |
|---|---|---|---|---|---|---|---|---|
| `open_plan_living_dining_kitchen.json` | `backend/recommendationEngine/Room.json` | 22 | 7 | 0 | 0 | 1 (15) | 20 | 4: livingRoom, diningRoom, kitchen, unidentified |
| `apartment_21_walls.json` | `backend/media/scans/3c01366b-f619-40f4-9794-8433133f310d/e5244c2f64aa45518d6017c9b4f44c22.json` | 21 | 3 | 0 | 2 | 1 (19) | 12 | 1: unidentified |
| `living_room_7_walls.json` | `backend/media/scans/41e1d0c5-e79a-46d0-b644-f3b18edf3449/532dfa7443714f55a0b39cf851174097.json` | 7 | 2 | 0 | 0 | 1 (9) | 7 | 1: livingRoom |
| `living_room_two_sections.json` | `backend/media/scans/9f24259c-70bb-4652-9d96-032b4c585f96/5350460191ea4f828fbb1032085e6058.json` | 8 | 0 | 0 | 1 | 1 (11) | 1 | 2: livingRoom, unidentified |
| `partial_2_walls.json` | `backend/media/scans/2e76eb85-6f68-41ff-9b91-08edf1b3c380/88f9d0e837db4c418797e4200058ed28.json` | 2 | 1 | 0 | 0 | 1 | 0 | 1 |

Why these are real captures: every file had a `coreModel` blob and a `referenceOriginTransform`
(both written only by the device encoder), float32 artefacts in the transforms, and non-round
coordinates. Every door carries a `parentIdentifier`. No wall has `curve` or `polygonCorners`.

What each one tests:

- `open_plan_living_dining_kitchen.json`: one CapturedRoom covering an open-plan
  living/dining/kitchen space, labelled only by section centers; one 15-corner floor polygon.
- `apartment_21_walls.json`: a single scan that walked through several spaces (bed, sofa,
  refrigerator in one capture); one 19-corner floor, 2 openings.
- `partial_2_walls.json`: an incomplete scan, 2 walls that do not close a loop, with a floor
  polygon. Tests the "walls do not enclose anything" path.

Changes from the source: the top-level `coreModel` key (an opaque base64 blob, 7 to 285 KB each)
was removed to save space. The remaining JSON was re-serialized compactly with Python `json`
in the original key order; parsing the result gives values identical to the original minus
`coreModel` (checked with `==` on the parsed objects). Nothing else was changed.

These files were not checked for overlap with each other: they come from separate upload
folders and share no wall identifiers with the largest one.
