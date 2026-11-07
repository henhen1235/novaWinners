# CV mapping: best method for iPhone scan -> scene.json, today

Date 2026-11-07, written for the 17:00 deadline. Question: after RoomPlan returned 2 tiny walls in
a ~14 x 14 m hall with a 3.6 m ceiling, what is the best way, including computer vision, to get
walls, rooms, doors, exits, obstacles and points_of_interest from an iPhone Pro, given a Mac M2
(16 GB, no CUDA), the phone itself, and OpenRouter on a small budget?

Short answer: keep ARKit's own mesh as the only geometry source (Mesh mode, already in
RoomScanner) and our grid pipeline as the only layout method. Add semantics from the keyframes
with an open-vocabulary 2D detector (OWLv2 on MPS), lifted to 3D with the keyframe's LiDAR depth
and ARKit pose. None of the learned layout models (SpatialLM, SceneScript, RoomFormer and
relatives) or 3D scene-graph systems run on this Mac today, and the published numbers do not beat
what we have on doors.

Scratch work (not in the repo): `/private/tmp/claude-501/-Users-karthikvattem-projects-nova/552f7d88-55d2-4092-aa65-2bf9d2393f83/scratchpad/cv/`
(`scripts/detect_bench.py`, `scripts/ocr.swift`, `scripts/tsdf_bench.py`, cloned repos under `src/`).

## 1. Recommended architecture

```
iPhone (RoomScanner, Mesh mode)                 Mac (Python, ingest_lidar)
-------------------------------                 ---------------------------------------------
ARKit sceneReconstruction                       mesh.ply -> Path B grid pipeline (unchanged)
  .meshWithClassification  --> mesh.ply           walls, rooms, doors, walkable, navmesh
  (per-face class)                                + face classes as evidence:
                                                    door faces  -> door candidates / confirm
KeyframeRecorder           --> frames.jsonl         window faces -> has_window
  kf_N.jpg (1440 long side)    kf_N.jpg             table/seat   -> obstacle kind "furniture"
  kf_N_depth.png (mm)          kf_N_depth.png
  [add] kf_N_conf.png          kf_N_conf.png    frames.jsonl -> detect (OWLv2, MPS, cached)
  intrinsics, camera_to_world                     -> lift: box center + median LiDAR depth
                                                     (fallback: ray vs ARKit mesh) -> world xyz
                                                  -> world -> scene frame (input_to_scene)
                                                  -> cluster across views (same class, 0.5 m)
                                                  -> points_of_interest; exit signs -> exit tags
                                                  -> optional VLM yes/no on crops (cached)
                                                frames -> vertex colours on the scan mesh
                                                  -> display.glb (photo-coloured)
```

Geometry source: ARKit mesh. It is metric, gravity aligned, fused on device from the same LiDAR
depth we would fuse ourselves, and the grid pipeline already reads it (`load._load_arkit_ply`,
face classes in `Scan.face_classes`). It does not fail the way RoomPlan did: RoomPlan's wall model
gave up in the big hall, while the mesh only needs the walls to have been within LiDAR range
(about 5 m) at some point. Scan guidance for the hall: walk the perimeter about 3 m from the walls,
then one pass through the middle.

Semantics source: OWLv2 (google/owlv2-base-patch16-ensemble, Apache-2.0) on keyframes, prompts
"exit sign", "fire extinguisher", "AED defibrillator", "fire alarm pull station", "door",
"stairs". It was the only detector of three that was right on all three test images (section 4).
ARKit face classes add door, window, table and seat evidence for free. The existing OpenRouter
path (`semantics.complete`, cached) can confirm low-confidence detections from crops.

Fusion into scene.json:
- POIs: each detection's box center pixel is lifted to 3D with the median of valid LiDAR depth in
  the central half of the box (depth PNG is 256 x 192, scale pixel coordinates by depth_w/img_w).
  Camera axes are ARKit's (x right, y up, -z forward), so with OpenCV pixels:
  `cam = ((u-cx)/fx*z, -(v-cy)/fy*z, -z)`, then `world = camera_to_world @ cam` (RadAR
  `enhance.patch_points` does exactly this). Our recorder writes `camera_to_world` row-major;
  RadAR's `T` is column-major (`reshape(4, 4, order="F")`). Do not copy RadAR's reshape. If the box
  has no valid depth (sign beyond 5 m, glass, black), cast the ray against the ARKit mesh with
  trimesh instead. Map world to the scene frame with `extensions.lidar.input_to_scene`.
- Merge detections of the same class within 0.5 m in XZ. Keep a cluster if it was seen in at least 2
  keyframes or once with score >= 0.45. Emit `PointOfInterest(kind=fire_exit_sign |
  fire_extinguisher | aed | alarm_pull)`; store the 3D height and view count in `extensions`.
- Exit signs: feed `[x, z, "Exit"]` into the existing `tags` path in `pipeline._exits` (nearest exit
  within 1.5 m gets the label). An exit sign over an interior door is evidence that the door leads
  to an exit route; mark it in `extensions` today, do not change `is_exit` without a human check
  (`tools.tag_exits` exists for that).
- Doors: ARKit door-class faces, clustered and projected to XZ, give door segments. Use them to
  confirm grid doors (and to add a door where the grid found a gap but no door) rather than as a
  primary source: ARKit labels open doorways inconsistently.
- has_window from window-class faces on the room boundary; furniture obstacles from table/seat
  faces (the grid already finds the obstacle, the class only sets `kind`).

What runs where:
- iPhone: ARKit mesh + classification, keyframe capture. No ML on device today.
- Mac CPU: grid pipeline (seconds), lifting, clustering, vertex colouring (seconds to a minute).
- Mac MPS: OWLv2, measured 0.69-0.76 s per keyframe on an M2 at its 960 px input. 200 keyframes is
  about 2.5 min; cache per image hash so reruns are free.
- OpenRouter (optional): crop verification and the room labels we already do. Not measured today
  (the repo .env key is not readable from this session); text calls in LLM_LATENCY.md cost
  $0.0003-0.0015 and 0.5-1.5 s, so 50 crops should stay under $0.10.

Accuracy to expect (honest): POI position error is dominated by ARKit drift over the walk and by
which pixel of the box the depth comes from. Plan on 0.2-0.5 m in XZ for a 14 m hall, fine for an
evacuation sim, not for compliance. Detector recall on real keyframes is unknown until the first
real scan: three web photos are a smoke test, not an evaluation. Exit signs far from the walked path
will be small in the frame and are the most likely misses.

## 2. Reuse from RadAR (github.com/camelCasedAditya/RadAR, commit 88b7a1a)

Read: `RadAR/Streamer.swift`, `RadAR/Wire.swift`, `server/relay/align.py`,
`server/relay/gpu_link.py`, `server/relay/protocol.md`, `hub/app.js` (patch and HD code),
`server/enhance/enhance.py`, `docs/superpowers/specs/2026-10-17-radar-design.md`. No LICENSE file;
it is the team's own code, so reuse is fine. Do not copy `server/aws/*.env` (instance ids).

| need | reuse | where | notes |
|---|---|---|---|
| dense coloured points from LiDAR depth | `upsample_depth` (joint bilateral upsampling of 256x192 depth to image resolution, no blending across depth steps), `unproject`, `place`, `voxel_keys` + 4 cm cell de-dup, `LIDAR_CONF = 2` | `server/relay/gpu_link.py` | numpy only, drop-in. Keep only confidence 2 pixels: RadAR found low-confidence LiDAR makes floaters. Our recorder does not save confidence yet (build step 1) |
| depth + confidence capture on iOS | `Wire.depthFrame`: copies `smoothedSceneDepth` depth and confidence row by row, handling `bytesPerRow` padding | `RadAR/Wire.swift` | our recorder uses `sceneDepth` (unsmoothed) and no confidence; switch to `smoothedSceneDepth` and add the confidence map |
| keyframe selection | move/turn gates (`Streamer.isMapFrame`: 2 cm/1 deg at 4 fps; `gpu_link.is_keyframe`: 25 cm or 15 deg for patches); `enhance.sharpness` (variance of Laplacian) and `best_frames` (sharpest frame within 5 frames that still covers 90% of the patch) | `Streamer.swift`, `gpu_link.py`, `server/enhance/enhance.py` | our recorder already gates at 0.5 m / 15 deg / 0.5 s, max 400. Missing piece is blur: add a motion gate on device (skip a candidate while angular speed > ~30 deg/s) and drop frames with low Laplacian variance in Python |
| photoreal display in Three.js | `onPatch`: unproject a depth grid with K, uv = pixel / image size, a quad only where all 4 corners have depth within 5% of each other, `applyMatrix4(T)`, `MeshBasicMaterial` with the photo; `hdMaterial` shader swaps in a sharper texture | `hub/app.js` lines 188-250, 458-515 | ports into `blueprint/viewer/src/main.ts` as an optional "photos" layer. Cost: about 5 MB GPU per patch (RadAR: ~1 GB for 189). Today, vertex colours on the scan mesh are cheaper (section 5, step 4) |
| multi-phone into one frame | convention only: both phones start on one floor mark facing one wall and ARKit origins are treated as identical. No registration code; collaborative sessions were rejected because they do not merge origins | design spec line 63 | at 1 deg of start yaw error, a point 14 m away is off by 0.24 m. If we ever need it: same convention plus a 4-DOF ICP (yaw + translation, gravity is shared) between the two meshes in Open3D, or ARWorldMap relocalization on the second phone |
| scale-free reconstruction to metric | `align.Aligner` (Sim(3) Umeyama with rotation averaged from orientations, works on straight walks), `scale_from_features` (ARKit feature points as metric depth) | `align.py`, `gpu_link.py` | only for non-LiDAR phones with lingbot-map. Not needed for iPhone Pro |
| session format | `.radar` = `RADAR2\n` then `u32 len, u32 t_ms, u8 type, body` records; kind 5 patches carry `{T, K, w, h, dw, dh, s}` + f16 depth grid + JPEG | `protocol.md` | not worth adopting (our frames.jsonl is simpler). Useful only as test data: a saved `.radar` with LiDAR patches converts to our keyframe folder in ~30 lines if a teammate has one locally |

lingbot-map (github.com/Robbyant/lingbot-map, commit b9dd1b1, Apache-2.0): a streaming
feed-forward reconstruction transformer (VGGT family, paged KV cache, FlashInfer recommended, SDPA
fallback). RadAR ran it on an L40S at ~52 ms per frame. It exists for phones without LiDAR. With
an iPhone Pro, ARKit depth is metric and better, and RadAR itself bypasses lingbot for LiDAR
phones. Not for today; MPS is untested.

## 3. Survey and verdict for today

Legend: Today = runs on the M2 Mac or the phone with pip/Xcode in under an hour of work.

### Structured layout from point clouds or video

| method | what it outputs | license | runs here today? | verdict |
|---|---|---|---|---|
| SpatialLM 1.1 (manycore-research, Qwen-0.5B / Llama-1B) | walls, doors, windows, oriented boxes as code text | weights CC-BY-NC-4.0 (Sonata encoder), Llama 3.2 or Apache-2.0 LLM | No. 1.1 imports `spconv` (wheels only for manylinux x86_64, checked with `uv pip install --dry-run`), `torch_scatter` (source build) and asserts `flash_attn` unless `enable_flash=False`; 1.0 needs torchsparse (CUDA build). Pins torch cu124 | later, on a CUDA box. Its own README reports zero-shot F1@0.25 on real video scans of 68 (walls), 47-49 (doors), 47-51 (windows); our grid detector is at 0.98 door recall on synthetic and has never been run on real data, so this is the comparison to make later, not a replacement today. Expects Z-up input |
| SceneScript (Meta) | same idea, language of layout commands | CC-BY-NC 4.0 | No: torchsparse v2.1, CUDA required (its README), trained on Aria semi-dense points | skip |
| RoomFormer, HEAT, Floor-SP, PolyDiffuse | room polygons (some variants doors/windows) from a top-down density image | mixed academic | No in practice: compiled CUDA deformable-attention ops (RoomFormer, HEAT; a slow PyTorch fallback exists in Deformable-DETR code), Floor-SP is minutes per scene, PolyDiffuse is a diffusion sampler; all trained on Structured3D | skip. Our grid already does density image to rooms (wall error 0.006-0.012 m on synthetic). Domain gap to ARKit is unmeasured |

### Open-vocabulary 3D scene graphs

ConceptGraphs, HOV-SG, OpenMask3D, OpenScene: all need CUDA (MinkowskiEngine for OpenMask3D and
OpenScene, gradslam and SAM/CLIP per frame for ConceptGraphs and HOV-SG) and minutes to hours per
scene on a GPU. Not feasible on the Mac today. What we want from them (objects in 3D with
open-vocabulary labels) we get from the 2D detect-and-lift pipeline above at a fraction of the
cost, because ARKit already gives us metric depth and poses.

### 2D detection on keyframes + lift with LiDAR depth (measured)

Throwaway venv (torch 2.x, transformers 5.22.2, ultralytics), Apple M2, three CC/public-domain
photos: a dim corridor with an EXIT sign over a door, a hallway wall with two fire extinguishers and
an open door (no exit sign), and a UK emergency exit. Times are per image after warm-up.

| model | M2 CPU | M2 MPS | results |
|---|---|---|---|
| YOLO-World v2-s (ultralytics) | 128-167 ms | 46-186 ms | exit sign found in both (0.32, 0.24), extinguishers 0.14 / 0.12, door missed in all three. Fast but low scores; usable only as a pre-filter |
| OWLv2 base ensemble (transformers) | 2.2-2.5 s | 0.69-0.76 s | exit sign 0.57 and door 0.47 (corridor); two extinguishers 0.63 / 0.63 and the open door 0.31 (hallway, no false exit sign); exit sign 0.59 (UK). Right on all three. Recommended |
| Grounding DINO tiny (transformers) | 5.6-7.8 s | 1.35-2.9 s | best door score (0.82) but false positives: "exit sign" 0.73 and "AED" 0.49 on the hallway image that has neither |
| Apple Vision `VNRecognizeTextRequest` (macOS, same API as iOS) | 17-320 ms | n/a | did not read "EXIT" in the full frame or in a 4x crop of the sign (red on brown, low luminance contrast). On the inverted red channel it read "EXIT" in both (fast mode, 17 ms full frame). Read "2ND FLOOR" fine. Useful later as an on-device hint with the red-channel trick, not as a detector |
| Florence-2 | not run | | needs `trust_remote_code` and a `flash_attn` import workaround on Mac; skipped for time |
| Gemini 2.5 Flash boxes via OpenRouter | not run | | no key readable from this session. Use as a verifier on crops, not as the detector: ~1 s and a fraction of a cent per call, and box accuracy from VLMs is coarser than OWLv2 |
| CoreML YOLO on device | not run | | needs a trained class set (exit sign, extinguisher) and conversion; later |

Three images is a smoke test. The first real hall scan is the actual evaluation.

### Depth and geometry

- ARKit mesh (have it): fused on device, classified, metric. Use it.
- Our own TSDF from keyframe depth (Open3D `ScalableTSDFVolume`): `open3d` installs on this Mac
  for Python 3.11 (checked), CPU integration. My synthetic timing script produced an empty volume
  (a convention bug I did not chase), so runtime is not measured. It would only re-do what ARKit
  did, with coloured voxels as the one gain. Not today.
- Dense coloured points from keyframes (RadAR `upsample_depth` + `place`): cheap, numpy only, and
  Path B already accepts point clouds (D24). Keep as a fallback if the mesh has holes.
- Depth Anything V2 (Small is Apache-2.0, Base/Large CC-BY-NC; a Core ML Small exists): only
  matters for phones without LiDAR. Not today.
- Gaussian splats: display only, training wants CUDA (gsplat) or a long Mac run, and the result is
  in another frame. Not today. Vertex colours or RadAR photo patches give most of the visual gain.

### How commercial apps get floor plans (from published material as I remember it, not re-checked today)

- Polycam: LiDAR "Room" mode uses Apple RoomPlan for its floor plans; photo mode is cloud
  photogrammetry; also Gaussian splats. Same RoomPlan failure modes as ours.
- Scaniverse (Niantic): on-device LiDAR meshes and on-device splats; no floor-plan product.
- Matterport: own cameras or iPhone capture, cloud reconstruction; schematic floor plans are a paid
  add-on produced with automation plus human drafters.
- Magicplan: ARKit-guided room capture (corners and walls, RoomPlan support), room by room, with
  manual editing.
- CubiCasa: phone video walk-through, floor plan from their ML plus human QA, delivered later rather
  than live.

Lesson: products either ride RoomPlan or keep a human in the loop. Our equivalent of the human step
is `tools.tag_exits`; keep it in the demo flow.

## 4. Build plan for the next ~2.5 hours

Steps 1 and 2 run in parallel (iOS and Python owners). Each is <= 45 min.

1. iOS, 30 min: KeyframeRecorder quality. In `ios/RoomScanner/RoomScanner/ScanModel.swift`:
   enable `.smoothedSceneDepth` (keep `.sceneDepth` if both are needed) and write the confidence
   map as `kf_N_conf.png` (u8, 0/1/2), copying RadAR `Wire.depthFrame` row handling; add a motion
   gate (skip a candidate frame while rotation since the previous ARFrame implies more than about
   30 deg/s) so keyframes are not blurred; add `confidence` to each frames.jsonl line. Done when one
   on-device export has mesh.ply, frames.jsonl and three files per keyframe. Then capture the hall:
   perimeter at ~3 m from the walls, one middle pass, 3-5 min.
2. Python, 45 min: `ingest_lidar/keyframes.py` with `load_keyframes(dir)` (row-major
   `camera_to_world`, intrinsics for the JPEG, depth PNG in mm scaled to image pixels, confidence
   >= 2 only) and `lift(u, v, frame)` (median depth in the central half of the box, ARKit camera
   axes, mesh ray fallback). First write the round-trip test: project a known world point into a
   synthetic camera, lift it back, assert < 1 cm. Then `detect(frames)` with OWLv2 on MPS, cached by
   image hash under `data/`; torch and transformers go in an optional requirements file, and
   `--no-detect` skips it.
3. Python, 40 min (after 2): clustering into `points_of_interest`, exit-sign tags into
   `pipeline._exits`, and ARKit face-class evidence (door segments to confirm or add doors, window to
   `has_window`, table/seat to obstacle `kind`). Wire it so `python -m ingest_lidar mesh.ply` picks
   up `frames.jsonl` next to the PLY. Validator must stay clean; `pytest -q` must stay green.
4. Python + viewer, 40 min (parallel with 3): vertex colours for the scan mesh from keyframes
   (per vertex, the nearest frontal keyframe whose depth agrees within 5 cm, i.e. not occluded;
   reuse `enhance.coverage` style projection), set `Scan.colors` so `display.write_display` writes
   them. The WS-A viewer replaces every material with a flat colour (`main.ts` line 407), so it
   needs `vertexColors: true` for the `scan` node and a small marker per `points_of_interest`
   entry. Tell the WS-A owner; it is their file.
5. Both, 30 min: run the real hall end to end, compare with the RoomPlan failure (walls, rooms,
   doors, POIs found vs what is in the hall), fix the worst issue, tag exits, write the numbers into
   STATUS.md.

## 5. What not to attempt today

- SpatialLM, SceneScript, RoomFormer, HEAT, Floor-SP, PolyDiffuse: CUDA-only dependencies,
  non-commercial weights for the first two, and no evidence they beat the grid on doors. Revisit on a
  rented CUDA box (RadAR's RunPod/EC2 scripts show the setup) as a comparison, not a dependency.
- ConceptGraphs, HOV-SG, OpenMask3D, OpenScene: CUDA and hours.
- Our own TSDF re-fusion, Gaussian splats, lingbot-map, Depth Anything: they solve problems an
  iPhone Pro does not have, or only improve the look.
- Multi-phone capture: one phone today. If needed later: RadAR's start-mark convention plus 4-DOF
  ICP refinement, or ARWorldMap relocalization.
- On-device ML (CoreML YOLO, Vision OCR as a detector): no time to train or tune; OCR misses red
  EXIT signs without the red-channel trick.
- Letting a detector set `is_exit` or add walls on its own: detections are evidence for a human
  check, not geometry.
