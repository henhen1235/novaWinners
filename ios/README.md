# RoomScanner

A one-screen iPhone app that scans a building floor room by room with Apple
RoomPlan and exports `structure.json`, which `ingest_lidar` converts into
`scene.json`.

## Requirements

- A Mac with Xcode 16 or newer (the project uses Swift 6 mode; built and checked with Xcode 27).
- An iPhone or iPad with a LiDAR scanner: iPhone 12 Pro / Pro Max or any
  later Pro model, iPad Pro 2020 or later. Other devices show a "needs a
  LiDAR scanner" message.
- iOS 17 or newer on the device.
- An Apple ID. The free personal team works; apps signed with it stop
  launching after 7 days, so just run it from Xcode again.

## Build and install (about 5 minutes)

1. Open `ios/RoomScanner/RoomScanner.xcodeproj` in Xcode.
   (The project is generated from `project.yml`. After editing that file, run
   `brew install xcodegen` once, then `cd ios/RoomScanner && xcodegen generate`.)
2. Select the RoomScanner target, open Signing & Capabilities, and pick your
   Apple ID under Team (Add an Account if it is not listed). If Xcode says
   the bundle identifier is not available, change `org.nova.RoomScanner` to
   something unique such as `org.nova.RoomScanner.yourname`.
3. Connect the phone with a cable and unlock it. On the phone, turn on
   Settings > Privacy & Security > Developer Mode and restart when asked.
4. Choose the phone as the run destination at the top of the Xcode window and
   press Cmd+R.
5. The first launch fails with "Untrusted Developer". On the phone go to
   Settings > General > VPN & Device Management, tap your Apple ID, tap Trust,
   then press Cmd+R again. Allow camera access when the app asks.

To check that the code compiles without a device or signing:

```sh
xcodebuild -project ios/RoomScanner/RoomScanner.xcodeproj -scheme RoomScanner \
  -sdk iphoneos -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

## Scan a floor (about 5 minutes per room)

All rooms of one floor must be scanned in one go: the app keeps the AR
session running between rooms so every room lands in the same coordinate
frame. Do not leave the app or lock the phone until you tap Finish.

1. Stand in the first room and tap Start. Walk along the walls, pointing the
   camera at walls, doors, windows and the floor. Move slowly and keep the
   room well lit. Make sure every door frame and every exit is seen.
2. Tap Stop. Wait for the 3D preview and the status line with the wall and
   door counts. If the room looks wrong, tap Start to rescan it.
3. Tap Add another room. Walk through the door to the next room, keeping the
   camera pointed ahead, and tap Start. Repeat steps 1 to 3 for each room.
   Corridors and halls count as rooms.
4. After stopping the last room, tap Finish. The app merges the rooms with
   RoomPlan's StructureBuilder. This takes a few seconds.
5. Tap Export. The files are written to Files > On My iPhone > RoomScanner >
   `scan-<time>/`.

The steps above are the Rooms mode (the switch at the top of the screen).
RoomPlan is built for home-size rooms: in a large open hall it returns only a
few small walls. Use Mesh mode for those.

## Scan a large space in Mesh mode

Mesh mode (the default) records the raw LiDAR mesh with ARKit scene
reconstruction, like Polycam or Scaniverse, with no size limit.

1. Pick Mesh at the top and tap Start. The reconstructed mesh is drawn over
   the camera image as it grows, so you can see what is covered.
2. Walk slowly, sweep floor to ceiling, go through every doorway. The status
   line shows anchors, triangles, floor area covered (m^2) and elapsed time.
   Pause stops the session; Start continues it in the same frame. Reset
   throws the mesh away and starts again.
3. Tap Export (it pauses first). The files go to Files > On My iPhone >
   RoomScanner > `mesh-<time>/`. Tap "Share 2 files" to AirDrop them.

Switching modes discards the other mode's unsaved scan.

`mesh.ply` is binary little-endian, world coordinates in meters, gravity
aligned with y up. Header:

```
ply
format binary_little_endian 1.0
comment rehearsal arkit mesh v1
element vertex N
property float x
property float y
property float z
element face M
property list uchar int vertex_indices
property uchar classification
end_header
```

Each face is a triangle (count byte 3, three int32 indices) followed by its
ARMeshClassification raw value: 0 none, 1 wall, 2 floor, 3 ceiling, 4 table,
5 seat, 6 window, 7 door. `scan_notes.json` has the usual keys plus
`"mode": "mesh"`, `"frame": "arkit_world_gravity_y_up"` and `"triangles": M`.

## Exported files

| file | content |
|---|---|
| `structure.json` | the merged floor (`CapturedStructure`), the converter's input |
| `structure.usdz` | 3D model of the merged floor, boxes and planes (`.parametric`), for viewing |
| `room_0.json`, `room_1.json`, ... | each room on its own (`CapturedRoom`). Always written, so a failed merge loses nothing |
| `scan_notes.json` | `{"captured_at": "2026-11-07T16:12:12Z", "device": "iPhone15,3 iOS 18.5", "rooms": 3, "exit_labels": [], "notes": ""}` |

If Finish reports "Merge failed", there is no `structure.json` or
`structure.usdz`; bring the `room_N.json` files and the error text.

The JSON layout is documented in `samples/roomplan/FORMAT.md`.

## Move the files to the Mac

1. After Export, tap "Share N files", choose AirDrop and pick your Mac. The
   files arrive in `~/Downloads`. (Without AirDrop: connect the cable, open
   Finder, select the phone, open the Files tab and drag the `scan-<time>`
   folder out of RoomScanner.)
2. Put them in the repo, one folder per scan:

```sh
mkdir -p samples/scans/<name>
mv ~/Downloads/structure.json ~/Downloads/structure.usdz ~/Downloads/scan_notes.json \
   ~/Downloads/room_*.json samples/scans/<name>/
```

3. Open `samples/scans/<name>/scan_notes.json` and write anything useful in
   `notes` (building, floor, which doors are exits).
