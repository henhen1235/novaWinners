# Test data for WS-A (blueprints) and WS-B (LiDAR / RoomPlan)

Checked 2026-11-07. "Verified" means the license and access route were read on the dataset's own page, repo or LICENSE file today. "Not verified" means the page was unreachable or not opened; treat those rows as leads. NC = non-commercial; fine for a hackathon demo, but do not ship derived data in a product.

## Ranked table

| # | Name | What | License | Access | Size | GT | Convert effort | WS | Checked |
|---|------|------|---------|--------|------|----|----------------|----|---------|
| 1 | buildingSMART Medical-Dental Clinic IFC | real 2-storey clinic, IFC2x3, metres; 269 spaces, 254 doors, 1080 walls, 3 stairs, 3124 space boundaries | CC BY 4.0 | direct (GitHub LFS); downloaded | 13 MB | full: walls, rooms, doors, stairs | low-med (ifcopenshell) | B (GT + synthetic scan), A (render plan) | verified |
| 2 | buildingSMART Duplex Apartment IFC | 2-storey duplex, IFC2x3, metres; 21 spaces, 14 doors, 57 walls, 2 stairs | CC BY 4.0 | direct; downloaded | 2.4 MB | full | low | B, A | verified |
| 3 | ResPlan | 17,000 residential plans, vector walls/doors/windows/rooms, metric metres, room graph with `via_door` edges | data CC BY 4.0 (GitHub LICENSE); Kaggle mirror says CC BY-NC-SA 4.0, conflict | direct: `ResPlan.zip` in github.com/m-agour/ResPlan | 100 MB | walls, doors, rooms, no raster | low (shapely in a pickle) | A (render to PNG = perfect GT) | verified |
| 4 | Swiss Dwellings v1.0 (Archilyse) | 42,207 apartments in 3,093 buildings; WKT walls, doors, windows, rooms with types, per floor | CC BY 4.0 | direct: zenodo.org/records/7070952 (`geometries.csv`, HTTP range works) | 792 MB CSV | walls, doors, rooms, metres (checked first rows) | low-med (group by floor_id) | A (render), B (extrude + synthetic scan) | verified |
| 5 | Existing RoomPlan samples (`samples/roomplan/`) | 12 real Apple sample rooms + exealabs kitchen + handmade structure | Apple sample MIT-style; Apache-2.0 | already in repo | small | RoomPlan objects | done (M3) | B | in repo |
| 6 | IMO MSC.1/Circ.1533 Annex 3 | Tests 1-12: corridor 2x40 m, room 8x5 m with 1 m exit (flow <= 1.33 p/s), corner, two 10x10 m rooms + 10x2 m corridor, 4-exit public room with 1000 persons, exit allocation, stair | IMO copyright, public circular | direct PDF; downloaded | 0.8 MB | geometry by text/figure | low (hand-author scene.json) | C, contract fixtures | verified (PDF read) |
| 7 | NIST TN 1822 | V&V suite for building evacuation models, modified IMO tests with layouts | NIST publication | direct PDF; downloaded | 1 MB | geometry by text/figure | low | C, fixtures | verified (PDF read) |
| 8 | RiMEA 4.1.1 | German pedestrian-model guideline, tests 1-16 (new: 12a-d bottleneck, 13 stairs) | none stated | direct PDF; downloaded | 1.9 MB | geometry by text | low | C, fixtures | verified (access) |
| 9 | ISPRS Indoor Modelling Benchmark | real point clouds: TUB1 (10 rooms), TUB2 (9 rooms, 10 doors), UoM (7 rooms, 14 doors), UVigo, Fire Brigade office, Grainger Museum; manual reference models | not stated | Nextcloud share dpv.uvigo.es/index.php/s/edzBCDDGkHjSWGC (scripted download got 401; try browser) | unknown | reference models (walls, doors) | medium (point cloud -> mesh or direct occupancy) | B | partly |
| 10 | Redwood Indoor LiDAR-RGBD | 5 real scenes (Apartment 69 m2, Bedroom 21, Boardroom 61, Lobby, Loft): TLS clouds + RGB-D reconstructions | public domain (cite Park 2017) | Google Drive links on redwood-data.org/indoor_lidar_rgbd/download.html; scripted fetch hit "quota exceeded" | recon mesh 80-139 MB, resampled TLS 0.2-0.5 GB | none (geometry only) | low (PLY into our loader) | B (real noisy mesh, multi-room apartment) | verified |
| 11 | CubiCasa5K | 5000 real raster plans (Finland), SVG polygons, 80 classes incl walls, doors, windows, rooms | CC BY-NC-SA 4.0 (NC) | direct: zenodo.org/records/2613548, one zip | 5.5 GB | walls, doors, rooms (pixels, no scale) | medium (SVG parse + scale) | A | verified |
| 12 | Schependomlaan | real Dutch house: design IFC + as-built point clouds (PLY/ASCII) | "scientific and academic purposes" (NC-ish) | buildingsmart-community repo + release zip | large, unknown | IFC GT + real scan of same building | medium | B (rare scan + BIM pair) | verified (README) |
| 13 | MSD (Modified Swiss Dwellings) | 5.3k building-complex floor plans as graphs + images | CC BY-SA 4.0 | Kaggle account | 17.4 GB | rooms, connectivity, walls | medium | A | verified (Kaggle API) |
| 14 | HouseExpo | 35k 2D layouts, verts in metres, room-category bboxes | repo MIT; derived from SUNCG (withdrawn) | direct: `json.tar.gz` in GitHub | 26 MB | outline + room bboxes, no doors | low | A, C (quick walkable areas) | verified |
| 15 | CVC-FP | 122 scanned plans, SVG GT walls, doors, windows, rooms | CC BY-NC 4.0 (NC) per project page | dag.cvc.uab.es (direct) | small | walls, doors, rooms | medium | A | search only |
| 16 | S3DIS / 2D-3D-S | 6 office areas, 271 rooms, per-point labels incl door/wall | terms form, research | form at 3dsemantics.stanford.edu (site unreachable today) | S3DIS few GB; 2D-3D-S 766 GB | rooms + doors | medium | B | not verified |
| 17 | Replica | 18 high-quality reconstructions (office_0-4, apartments) with semantics | Replica Research Terms (NC) | `download.sh`, no form | ~tens of GB total | semantic mesh | low-med | B | verified |
| 18 | ROBIN | 510-ish plan images in class folders | GPL-3.0 repo | direct zip in GitHub | 16 MB | room-count class labels only | n/a | A (smoke test images) | verified (access) |
| 19 | FloorPlanCAD | CAD drawings, SVG, 30 classes | CC BY-NC 4.0 (NC); project discontinued | floorplancad.github.io, HF mirrors | ~GBs | symbols incl doors, walls | medium | A | search only |
| 20 | ZInD | 1,575 homes, 360 panos, 2D floor plans | Zillow terms (NC, academic) | Bridge account | 40 GB | rooms, doors, windows | medium | A | verified (README) |
| 21 | Structured3D | 3,500 synthetic houses with floorplan annotations | Structured3D Terms of Use | agreement form | large | full | medium | A, B | verified (README) |
| 22 | Hilti SLAM 2023 | handheld/robot LiDAR of construction sites | CC BY-NC-SA 3.0 (NC) | Hugging Face, direct | 9-63 GB per sequence | trajectory only | high | none | verified |
| 23 | ScanNet, ScanNet++, Matterport3D, HM3D, Gibson, 3RScan, ARKitScenes | indoor RGB-D / meshes | all NC terms; forms except ARKitScenes | forms / email | large | varies | medium | B | READMEs read |
| 24 | R2V (Raster-to-Vector) | code MIT; images not shared, only 100k predicted vectors | n/a | Google Drive | n/a | predicted, not GT | n/a | none | verified |

Skipped: IfcOpenShell/files (bug-repro fragments, no license), buildingSMART Certification-datasets (bSI copyright, no open license), opensourceBIM/ifc-files (CC BY-ND per search: no derivatives), KIT FZK-Haus / Institute (conflicting "unrestricted use" vs CC BY 4.0 statements; check ifcwiki.org KIT_IFC_Examples before use), Sketchfab/Polycam CC-BY scans (downloads need a free account and per-model checks; not done today).

## Top picks per workstream

### WS-B (LiDAR / RoomPlan)
1. Clinic + Duplex IFC (downloaded). Convert to ground-truth scene.json, then feed the GT through `tools/make_synthetic_scan.py` (or extrude walls/slabs from the IFC mesh directly) to get a scan with exact truth for `tools/score_against_truth.py`. The Clinic is the only open, real, non-residential multi-room building with corridors and many doors we found.
   Next: `pip install ifcopenshell`; write `tools/ifc_to_scene.py` (route below); score our pipeline on both.
2. Redwood Indoor Apartment + Boardroom reconstructions: real scanner noise, holes and clutter, public domain. No labels, so use for robustness (orient, walkable, exits) and hand-tag 1 truth file.
   Next: download in a browser (Drive blocked scripted fetch), put under `data/external/redwood/`, run `python -m ingest_lidar`.
3. ISPRS Indoor Modelling Benchmark: real office floors with reference models, closest match to "scan an office and get rooms + doors".
   Next: open the dpv.uvigo.es share in a browser, grab TUB1 (low clutter) and UoM (Zeb-1 handheld, 14 doors). Check what format the reference models use and whether a license file is inside.

### WS-A (blueprint images)
1. ResPlan: 100 MB, vector + metric, CC BY 4.0 on GitHub. Render plans to PNG (walls black, door arcs) at a known px/m and score WS-A against exact truth. Note the Kaggle CC BY-NC-SA label; cite GitHub's LICENSE.
   Next: `curl -LO https://github.com/m-agour/ResPlan/raw/main/ResPlan.zip`, use `resplan_utils.py` to load, write a 50-line renderer.
2. Swiss Dwellings: metric WKT, CC BY 4.0, whole multi-unit buildings (stairs, corridors per floor), direct download. Range-fetch the first few MB to get a handful of buildings without the full 792 MB.
   Next: `curl -r 0-20000000 -o sd_head.csv https://zenodo.org/records/7070952/files/geometries.csv?download=1`, drop the last partial line, group by (building_id, floor_id).
3. CubiCasa5K (NC): the only big set of real raster plan images with SVG GT. Use for realism tests only; it is 5.5 GB in one zip, so start the download early.
   Plus: render the Clinic IFC storeys to PNG as a non-residential blueprint test with exact truth.

### Shared: standard evacuation scenes
Author 4 tiny scene.json fixtures from IMO Circ.1533 Annex 3 (Test 1 corridor 2x40 m, Test 4 room 8x5 m with 1 m exit, Test 8 two 10x10 m rooms + 10x2 m corridor, Test 9 public room with 4 exits) and RiMEA test 12 (bottleneck). They double as WS-C sanity checks (Test 1: 40 s at 1 m/s; Test 4: flow <= 1.33 p/s).

## IFC to scene.json route (ifcopenshell)

```python
import ifcopenshell, ifcopenshell.geom, ifcopenshell.util.element as ue
f = ifcopenshell.open(path)                       # both files declare METRE
s = ifcopenshell.geom.settings(); s.set(s.USE_WORLD_COORDS, True)
for st in f.by_type("IfcBuildingStorey"): ...     # levels: Elevation, ceiling from next storey
spaces = f.by_type("IfcSpace")                    # rooms: footprint = shape verts projected, LongName -> name/type
walls  = f.by_type("IfcWall")                     # IFC2x3 also IfcWallStandardCase (subtype)
doors  = f.by_type("IfcDoor")                     # OverallWidth; host wall via FillsVoids -> IfcOpeningElement -> VoidsElements
bounds = f.by_type("IfcRelSpaceBoundary")         # door <-> space pairs give Door.connects; door with one space = exit candidate
stairs = f.by_type("IfcStair")
shape  = ifcopenshell.geom.create_shape(s, el)    # shape.geometry.verts / faces, world coords
```
Footprints: slice each wall/space mesh at floor + 1 m with trimesh (already a dependency) and take the shapely union. Axes: IFC is Z up; map (x, y, z)ifc -> (x, z, -y)scene to keep a right-handed Y-up frame. The same meshes, exported with trimesh, are the synthetic "scan" input for WS-B.

## Downloaded (data/external/, see its README.md)
- `ifc_clinic/Clinic_Architectural.ifc` (13 MB, CC BY 4.0)
- `ifc_duplex/Duplex_A_20110907.ifc` (2.4 MB, CC BY 4.0)
- `imo_msc1_circ1533/MSC.1-Circ.1533.pdf`, `nist_tn1822/NIST.TN.1822.pdf`, `rimea/rimea-4.1.1-d-e.pdf` (reference PDFs, not for redistribution)
- Not downloaded: Redwood (Drive quota), ISPRS (share needs a browser), ResPlan / Swiss Dwellings (100 MB / 792 MB, left as one-command next steps).
