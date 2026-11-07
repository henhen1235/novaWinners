# 60-second demo: phone scan in, walkable 3D out

Prep (before recording): a real scan in `samples/scans/<name>/` (RoomScanner
`structure.json`, or a Polycam/3D Scanner GLB/OBJ), the viewer installed
(`cd blueprint/viewer && npm install`). Fallback if no real scan:
`python -m tools.make_synthetic_scan --layout tiny_office --seed 0 -o samples/scans/synthetic`.

| t (s) | Show | Say |
|---|---|---|
| 0-10 | iPhone running RoomScanner, sweeping a room, walls and doors appearing | "We scan the building with the LiDAR in a normal iPhone. Two minutes per floor." |
| 10-20 | Terminal: `python -m ingest_lidar samples/scans/<name>/structure.json -o out/demo` finishing in about 2 s, then `python -m contract.validate_scene out/demo/scene.json` printing OK | "The scan becomes a validated building model: walls, rooms, doors, exits, furniture, and the walkable floor." |
| 20-35 | `out/demo/debug/overview.png`: green walkable, black walls and furniture, red exits | "Walkable space is the open floor shrunk by a person's radius. Every room is checked to reach an exit." |
| 35-55 | Viewer (`npx vite` in blueprint/viewer, open `/?glb=/lidar/display.glb&scene=/lidar/scene.json`): orbit the scan, press Top, click a far room; the green route draws itself to the nearest exit with distance and time | "This is what the evacuation sim runs on. Here is the walking route from the far office to the nearest exit." |
| 55-60 | Hand-off to the sim | "Now we fill it with LLM occupants and pull the fire alarm." |

Numbers to quote (synthetic benchmark, 30 scans, see STATUS.md): door recall 0.99,
walkable IoU 0.95, wall error 6 mm median, 2 s per scan.
