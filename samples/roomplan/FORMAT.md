# RoomPlan JSON format

What `JSONEncoder().encode(capturedRoom)` and `JSONEncoder().encode(capturedStructure)`
produce, for the Python converter (`ingest_lidar/roomplan_to_scene.py`). Everything
below was checked against real exports or against Apple's own decoder; the few
things that could not be checked are listed under "Unverified".

## How this was verified

- API surface: the RoomPlan Swift interface shipped in Xcode 27
  (`iPhoneOS27.0.sdk/.../RoomPlan.framework/Modules/RoomPlan.swiftmodule/arm64e-apple-ios.swiftinterface`,
  module version 2.17). All `Codable` conformances there are hand-written
  (`init(from:)` / `encode(to:)`), so the layout had to be read from real files.
- Docs: https://developer.apple.com/documentation/roomplan (CapturedRoom,
  CapturedStructure, StructureBuilder, Surface.polygonCorners, parentIdentifier,
  USDExportOptions), WWDC22 session 10127 sample "Create a 3D model of an interior
  room by guiding the user through an AR experience", WWDC23 session 10192
  "Explore enhancements to RoomPlan" (https://developer.apple.com/videos/play/wwdc2023/10192/)
  and its sample "Merging multiple scans into a single structure".
- Real files: the 12 real scans in this folder plus two unlicensed public exports
  that were read but not copied (see "Samples").
- Apple's decoder: a throwaway simulator app (iOS 26.5 runtime) decoded every
  sample here with `JSONDecoder().decode(CapturedRoom.self / CapturedStructure.self)`,
  re-encoded them, and decoded hand-written variants to settle the transform
  order, the `completedEdges` encoding and the `curve` encoding.

## Units and frame

- Meters. Right-handed, Y up (gravity aligned), the ARKit world frame of the
  capture session. Same convention as `contract/` (floor plan = XZ plane), so
  positions map across unchanged: no axis flip, no mirroring.
- The origin is where the phone was when the ARSession started, so y = 0 is
  roughly chest height. Floors sit at y = -1.29 to -1.49 in the real samples.
  Use the floor surfaces (or the lowest wall bottom) to find floor height.
- Rooms captured in one continuous ARSession share this frame. Apple's 11
  `apple_myhome` rooms tile one house in x/z; their floor heights differ by up
  to 9 cm (-1.391 to -1.478), which is tracking drift, not real steps.

## Transform layout

`transform` is 16 floats, **column-major** (simd_float4x4 columns flattened in
order). Proven by decoding `[1, 2, ..., 16]`: RoomPlan reads `columns.0 = (1, 2, 3, 4)`
and `columns.3 = (13, 14, 15, 16)`.

```python
M = np.array(t, dtype=float).reshape(4, 4).T   # now M @ [x, y, z, 1] maps local -> world
x_axis, y_axis, z_axis, center = M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3]
# equivalently: t[0:3] local x, t[4:7] local y, t[8:11] local z, t[12:15] translation
```

The translation is the center of the element's bounding box. Rotations in real
scans are yaw only: local y = (0, 1, 0) for every wall, door, window, opening
and object (101/101 walls, 63/63 doors, windows and openings, 131/131 objects).

## Top level

CapturedRoom (keys as written by the device; all files here have `version: 2`):

| key | type | notes |
|---|---|---|
| `walls`, `doors`, `windows`, `openings`, `floors` | list of Surface | |
| `objects` | list of Object | |
| `sections` | list of Section | room-type labels, iOS 17+ |
| `story` | int | 0 in every sample |
| `version` | int | 2 |
| `coreModel` | string | opaque base64 blob, ~280 KB per room. Undocumented. Ignore. Absent from `exealabs_kitchen` (stripped by its author) and absent when re-encoded on a simulator. |
| `referenceOriginTransform` | 16 floats | undocumented, not exposed in the Swift API. A yaw plus a few cm of translation in device files, identity on the simulator. Ignore. Absent from the iOS 17.0-era Apple files. |

There is **no top-level `identifier`** in the JSON even though the Swift type has
one; the decoder makes one up.

CapturedStructure (iOS 17+, what the app writes as `structure.json`). Key set
taken from RoomPlan's own encoder (`encode(capturedStructure)` on the simulator):

| key | type | notes |
|---|---|---|
| `rooms` | list of CapturedRoom | each encoded exactly as above |
| `walls`, `doors`, `windows`, `openings`, `floors` | list of Surface | merged over all rooms |
| `objects` | list of Object | merged |
| `sections` | list of Section | |
| `version` | int | 2 |

No `identifier`, no `story` at the structure level (stories live on each element).

## Surface (walls, doors, windows, openings, floors)

```json
{"category": {"door": {"isOpen": false}}, "completedEdges": [], "confidence": {"high": {}},
 "curve": null, "dimensions": [0.76, 2.03, 0], "identifier": "EA9C2034-...",
 "parentIdentifier": "0DB0C593-...", "polygonCorners": [], "story": 0,
 "transform": [-0.878, 0, -0.479, 0, 0, 1, 0, 0, 0.479, 0, -0.878, 0, -0.523, -0.312, 2.169, 1]}
```

| key | type | meaning |
|---|---|---|
| `category` | object | `{"wall":{}}`, `{"door":{"isOpen":bool}}`, `{"window":{}}`, `{"opening":{}}`, `{"floor":{}}` |
| `confidence` | object | `{"high":{}}`, `{"medium":{}}` or `{"low":{}}` |
| `dimensions` | [x, y, z] floats | local-frame size; z is always 0 (surfaces have no thickness) |
| `transform` | 16 floats | column-major, see above |
| `identifier` | string | uppercase UUID |
| `parentIdentifier` | string or null | doors, windows, openings: the wall they sit in (always set in the samples); walls and floors: null |
| `polygonCorners` | list of [x, y, z] | local plane coordinates, z ~ 0; `[]` for rectangular walls and for all doors/windows/openings |
| `completedEdges` | list | an unordered set: `[{"top":{}}, {"left":{}}]`; values top/right/bottom/left. `[]` in all 14 real files |
| `curve` | object or null | null in all real files; format below |
| `story` | int | |

### Walls

- `dimensions = [length, height, 0]`. Local x runs along the wall, local y is up,
  local z is the wall normal.
- Centerline on the floor plan: `center +- x_axis * length / 2`, take `[x, z]`.
  Bottom = `center.y - height / 2`, top = `center.y + height / 2`.
- Local +z points **into the scanned room**: in 101/101 real walls the point
  `center + 0.3 * z_axis` lies inside that room's floor polygon. In a merged
  structure a wall between two rooms can only face one of them.
- Thickness is not captured (dimensions z = 0).
- Non-rectangular walls (sloped ceilings, partial-height walls) carry
  `polygonCorners` in local (x, y): centered on the transform, x in
  [-length/2, length/2], y in [-height/2, height/2]. 6 of 10 walls in one public
  export had them; none of the Apple walls do.

### Doors, windows, openings

- `dimensions = [width, height, 0]`. Same axes as the parent wall: the center
  lies in the parent wall plane (< 5 cm off, 63/63) and local z equals the
  parent wall's local z (63/63).
- Sill height = `center.y - height / 2 - floor_y` (0 for doors and openings).
- `isOpen` is whether the leaf was seen open; it says nothing about swing direction.

### Floors

- Local z = world up; the polygon lies in the floor's local x-y plane.
- World corners: `M @ [cx, cy, 0, 1]` for each `[cx, cy, cz]` in `polygonCorners`.
- Winding (14/14 real files): shoelace area over **world [x, z]** is positive;
  over local [x, y] it is negative.
- `dimensions` is a loose bound, not the polygon size (Diningroom: dimensions
  10.28 x 7.61 m, polygon extent 5.8 x 7.5 m). Use `polygonCorners`.

### Curve (curved walls)

Format confirmed by Apple's decoder, never seen in a real export:

```json
{"center": [0.5, 0.25], "radius": 2.0,
 "startAngle": {"value": 0.1, "unit": {"symbol": "rad", "converter": {"coefficient": 57.29577951308232, "constant": 0}}},
 "endAngle":   {"value": 1.2, "unit": {"symbol": "rad", "converter": {"coefficient": 57.29577951308232, "constant": 0}}}}
```

Angles are Foundation `Measurement<UnitAngle>`: degrees = value * coefficient +
constant. Plain numbers for the angles fail to decode. Which local plane `center`
lives in is not documented.

## Object

```json
{"attributes": {"StorageType": "cabinet"}, "category": {"storage": {}}, "confidence": {"low": {}},
 "dimensions": [0.94, 1.72, 0.86], "identifier": "7E21C024-...", "parentIdentifier": null,
 "story": 0, "transform": [0.972, 0, -0.234, 0, 0, 1, 0, 0, 0.234, 0, 0.972, 0, -1.785, -0.467, -1.544, 1]}
```

- `category`: `{"<name>":{}}` with name one of storage, refrigerator, stove, bed,
  sink, washerDryer, toilet, bathtub, oven, dishwasher, table, sofa, chair,
  fireplace, television, stairs.
- `dimensions = [width, height, depth]` along local x, y, z. Box center at the
  translation; bottom = `center.y - height / 2` (on the floor for 101/131
  objects, the rest are stacked, such as a sink on a cabinet).
- Front = local **+z**: for objects standing against a wall, +z points away
  from the wall in 70 of 81 cases (the 11 others: 5 storage, 2 beds, 2 sofas, 1 oven, 1 toilet, mostly in corners where the nearest wall is ambiguous).
- `parentIdentifier`: another object (chair -> table, sink -> storage,
  sofa -> sofa) or, rarely, a wall (fireplace).
- `attributes`: an object keyed by attribute type: `{}`, `{"StorageType": "shelf"}`,
  `{"ChairType": "dining", "ChairLegType": "four", "ChairArmType": "missing", "ChairBackType": "existing"}`,
  `{"TableType": "coffee", "TableShapeType": "circularElliptic"}`, `{"SofaType": "lShapedExtension"}`.
- Stairs: category `{"stairs":{}}`, a bounding box with no step detail. Not
  present in any real sample here.

## Section

`{"center": [x, y, z], "label": "livingRoom", "story": 0}`. The label is a plain
string: livingRoom, bedroom, bathroom, kitchen, diningRoom, unidentified.
`center` is a world-frame point for the labeled area.

## Samples

| path | what | provenance | license |
|---|---|---|---|
| `apple_myhome/<Room>/capturedRoom.json` (11 rooms) | Real device scans of one house in one shared frame: 67 walls, 29 doors, 11 windows, 9 openings, 82 objects. iOS 17.0 era (no `referenceOriginTransform`). | Apple sample "Merging multiple scans into a single structure" (WWDC23 10192), https://docs-assets.developer.apple.com/published/3e2b731b0ef7/MergingMultipleScansIntoASingleStructure.zip, folder `RoomPlanMultiscanMerging/MyHome`, present since the sample's first commit 85d1bf8d (2023-05-20); copied verbatim from the zip's git HEAD f9ace539. The current zip's working tree omits the folder; `git archive HEAD` inside it restores it. | Apple sample code license (MIT style), `apple_myhome/LICENSE.txt` |
| `exealabs_kitchen/kitchen_real_01.json` | Real kitchen, iPhone 13 Pro Max, 4 walls, 2 openings, 19 objects, no doors or windows. `coreModel` stripped by its author. | https://github.com/AvnehSBhatia/ExeaLabsNvidiaHacks/blob/bd4e791e2d531cb93ef8c6c2a8154d5ff7539ff6/tests/fixtures/roomplan/kitchen_real_01.json ("consented teammate capture" per that repo's fixture README) | Apache-2.0, `exealabs_kitchen/LICENSE` |
| `handmade_structure.json` | **Hand-made** CapturedStructure: 2 rooms (x 0..5 and 5..9, z 0..4, floor y = -1.40, walls 2.6 m), 7 merged walls, 2 doors (interior open door in the shared wall at x = 5, z = 1; exit door in room A's south wall at x = 1.2), 1 window, 1 opening, table, storage, stairs. | Written for this repo. Follows the conventions above (normals into rooms, floor winding, object fronts). Apple's decoder accepts it and re-encodes it identically apart from `completedEdges` order. | same as this repo |

Read but not copied (no license, so not redistributable):
https://github.com/BaidetskyiYurii/RoomPlanDemo/blob/main/RoomPlanDemo/CapturedRoom.json
(has `coreModel`, `referenceOriginTransform` and non-rectangular walls with
`polygonCorners`) and
https://github.com/holokcheung/touchspace/blob/main/backend/geometry/samples/test-roomplan.json
(20 walls, open and closed doors).

## Unverified (needs a device run)

- How `StructureBuilder` fills the merged lists. The simulator returns an
  empty structure, and no public device-made `structure.json` was found. The
  hand-made sample assumes the shared wall and the interior door appear once in
  the top-level lists while each `rooms[i]` keeps its own copies. Until a real
  file exists, the converter should read the top-level lists and treat `rooms`
  as optional context.
- Whether merged surfaces keep the identifiers of the room they came from.
- Non-empty `completedEdges` and non-null `curve` in a real export.
- What `referenceOriginTransform` encodes.
- Stairs and multi-story (`story` != 0) output.
