# Decisions

## 2026-11-07

- Nova is its own git repo. The Desktop folder is already a repository, and this project must not commit those files.
- Python 3.11.17 is installed with `uv` because the system interpreter is 3.13. The spec asks for 3.11.
- The OpenRouter key lives in `.env` as `OPENROUTER_API_KEY`. Pipelines load it with `python-dotenv`. It is gitignored. `.env.example` lists the names only.
- Vision calls, when enabled, go to `https://openrouter.ai/api/v1`. The model is `OPENROUTER_MODEL`, then `ANTHROPIC_MODEL`, then `anthropic/claude-sonnet-4.5`.
- PyMuPDF drawing and text coordinates already use a top-left origin with y increasing downward, matching the raster. The loader multiplies by the DPI zoom and does not flip y. World `z` is that image y divided by `px_per_m`.
- Leak check: a room's walkable sample is `walkable ∩ room.buffer(0.30 m)`. That reaches across a normal wall. Eroded interiors sit 0.25 m inside the boundary, so a solid wall does not raise `LEAKY_WALL_SUSPECTED`.
- Navmesh components on different floors are joined when a stair or link has endpoints within 0.5 m of triangles on each level. Otherwise a valid upper floor would look disconnected from the ground-floor exit.
- Correction operations store source-image pixels at the pipeline's fixed 200 DPI raster. `set_scale` stores pixel endpoints plus a meter distance. Other operations are applied after that scale is known.
- Room names are written on the overlay and in `scene.json`. The display glTF does not extrude text meshes.
