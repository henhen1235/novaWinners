# Decisions

## 2026-11-07

- Nova is its own git repo. The Desktop folder is already a repository, and this project must not commit those files.
- Python 3.11.17 is installed with `uv` because the system interpreter is 3.13. The spec asks for 3.11.
- The OpenRouter key lives in `.env` as `OPENROUTER_API_KEY`. Pipelines load it with `python-dotenv`. It is gitignored. `.env.example` lists the names only.
- Vision calls, when enabled, go to `https://openrouter.ai/api/v1`. The model is `OPENROUTER_MODEL`, then `ANTHROPIC_MODEL`, then `anthropic/claude-opus-5.5`.
- PyMuPDF drawing and text coordinates already use a top-left origin with y increasing downward, matching the raster. The loader multiplies by the DPI zoom and does not flip y. World `z` is that image y divided by `px_per_m`.
- Leak check: a room's walkable sample is `walkable ∩ room.buffer(0.30 m)`. That reaches across a normal wall. Eroded interiors sit 0.25 m inside the boundary, so a solid wall does not raise `LEAKY_WALL_SUSPECTED`.
- Navmesh components on different floors are joined when a stair or link has endpoints within 0.5 m of triangles on each level. Otherwise a valid upper floor would look disconnected from the ground-floor exit.
- Correction operations store source-image pixels at the pipeline's fixed 200 DPI raster. `set_scale` stores pixel endpoints plus a meter distance. Other operations are applied after that scale is known.
- Room names are written on the overlay and in `scene.json`. The display glTF does not extrude text meshes.
- SciPy 1.15.3 crashes on this machine while importing `scipy.sparse.linalg._propack`. The pin is 1.16.3. Display meshes use vertex colors so export does not import `scipy.sparse`.
- Walkable polygons are simplified by 2 cm before triangulation. Earcut otherwise emits sliver triangles that rounding to 0.1 mm turns into degenerate faces, and dropping those faces splits the navmesh.
- Double-line walls are paired when the strokes are parallel, 0.10–0.40 m apart, and overlap. Endpoints within 4 cm of another wall are projected onto it so polygonize can close the room. That passed the vector score on all 50 seeds, so no fallback was required for double-line vector plans.
- Raster segmentation tried three approaches and still misses the raster score gate, so the classical sealed-mask result is what ships:
  1. Adaptive threshold, distance-transform thickness, skeleton, Hough lines. Corners stayed open and polygonize returned no rooms.
  2. Extending Hough endpoints to nearby intersections. Single-line pages gained a few rooms; noisy pages exploded into dozens of false rooms.
  3. Morphological close to seal door gaps, flood-fill to drop the page exterior, contours for rooms, ink-gap length for doors. Single-line synthetic PNGs land within one room of the truth and about 0.80–0.92 walkable IoU. Door recall stays below 0.80, and the navmesh often fails `NAVMESH_INVALID` because a component does not touch an exit. Hairline double-line rasters are worse. Vector ingest remains the supported path.
- `--ml` does nothing unless `data/cubicasa/model_weights.pth` exists. Those weights were not downloaded.
