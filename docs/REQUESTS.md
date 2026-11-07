# Requests between agents

## From Andrew's agent (14:55)

1. Owner of `requirements.txt` (Karthik): `certifi==2026.10.5` is not on PyPI, so
   `pip install -r requirements.txt` fails on a clean machine (versions available
   include 2026.10.18, 2026.10.19, 2026.10.23). Please pin one that exists. Not changed by
   Andrew's agent.
2. FYI, no action needed if the demo laptop runs Python 3.11: on Python 3.14 (Windows)
   `scikit-image==0.25.2` has no wheel and fails to build, so the full demo build was not
   verified there.
3. Karthik: before the demo, run `scripts/demo/setup.sh --skip-install` on the laptop that
   has `samples/scans/hall_mesh/mesh.ply` and `out/loop/demo_hall/` (and `demo_tepper/`), so
   the hall scan and the recorded LLM loops are copied into `blueprint/viewer/public/`.
   Then `scripts/demo/start.sh` and walk `docs/DEMO.md`. Tepper loop numbers are not in the
   README or SUBMISSION.md yet because `out/loop/demo_tepper/report.md` was not available.
