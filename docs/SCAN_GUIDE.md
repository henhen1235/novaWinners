# Scan guide (iPhone Pro LiDAR)

Budget 10 to 20 minutes per floor. Do a 5-minute rehearsal of one room first
and run it through the pipeline before the real scan.

## Before you start
- Prop every door open. A closed door looks like a wall and the room becomes unreachable.
- Turn on the lights. Avoid direct sunlight on the floor.
- Note mirrors and glass walls; they create phantom rooms and holes.

## While scanning
- Walk slowly, about 1 m from the walls.
- Sweep each room floor to ceiling. The pipeline needs the floor and the
  top of the walls (walls are told apart from furniture by reaching the ceiling).
- Walk through doorways and overlap rooms through them, so each doorway has
  floor scanned under it.
- Scan the paths to every exit and step just outside each exit door.
- Photograph each exit sign and write its label in `scan_notes.json`
  (`exit_labels`).

## After scanning
- Export immediately (RoomScanner app: Export; Polycam / 3D Scanner App: GLB
  or OBJ in meters). Keep a backup of the raw file.
- AirDrop to the Mac into `samples/scans/<name>/`.
- Run: `.venv/bin/python -m ingest_lidar samples/scans/<name>/<file> -o out/<name>`
- Look at `out/<name>/debug/overview.png` (green walkable, black occupied,
  blue unknown pockets, red exits, blue lines interior doors).
- Missing or mislabeled exits: `.venv/bin/python -m tools.tag_exits out/<name>`,
  click each exit, Save, then rerun with `--exit-tags out/<name>/exit_tags.json`.
