# 60-second demo

1. Open `samples/blueprints/office_vector/plan.pdf`. The sheet is an asymmetric office: West Annex on the left, a corridor, four more offices, two EXIT marks, a 0–5 m scale bar.
2. In a terminal at the repo root, with the virtualenv active:

```bash
python -m ingest_blueprint samples/blueprints/office_vector/plan.pdf -o out/office --no-llm
python -m contract.validate_scene out/office/scene.json
```

The validator prints `[]`. `out/office/overlay.png` draws the recovered rooms and doors on the page.
3. Copy the glTF and scene into the viewer and start it:

```bash
mkdir -p viewer/public/office
cp out/office/display.glb out/office/scene.json viewer/public/office/
cd viewer && npm install && npm run dev
```

4. Orbit the model, then press Top. Click a room floor. The yellow path runs to an exit and the readout shows meters and seconds at 1.3 m/s.
5. Optional: `python tools/path_probe.py out/office/scene.json --room r_L1_001` prints the same distance without a browser.
