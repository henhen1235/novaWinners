# DATA

No datasets are committed. Download scripts write to `data/` (gitignored).

| Dataset | Used | License | Notes |
|---------|------|---------|-------|
| Synthetic scans (`tools/make_synthetic_scan.py`) | yes, primary test data | ours | regenerated from seeds, never committed |
| Team-captured iPhone scans (`samples/scans/`) | planned, demo | ours | 2 curated scans only, small files |
| Apple RoomPlan sample exports | yes, tests | see `samples/roomplan/` provenance | |
| heyday RoomPlan scans (`samples/roomplan/heyday/`) | yes, tests | MIT, Copyright (c) 2025 Edward Ke | 5 real CapturedRoom JSONs from github.com/edwardzke/heyday@eed4bef0, coreModel stripped; LICENSE and PROVENANCE.md in the folder |
| Matterport3D, HM3D, Gibson, ScanNet | not used | research-only / account-gated | NON-COMMERCIAL, skipped for the hackathon |
| ARKitScenes | not used | Apple research license | NON-COMMERCIAL, skipped |
| CMU ESIM floor plans, Tepper Building floors 1-5 (`data/cmu/raw/tepper/`) and Mudge House floors A-3 (`data/cmu/raw/mudge/`) | yes, demo scenes `samples/cmu/tepper/`, `samples/cmu/mudge/` | CMU copyright, Andrew ID login, no redistribution (cmu.edu/legal) | source PDFs stay local and gitignored; only derived geometry is committed, see each folder's PROVENANCE.md |
| CMU public building maps (Cohon Center, Forbes Beeler housing PDFs on cmu.edu) | WS-A failure cases only | CMU copyright, no redistribution | URLs in `blueprint/FAILURES_CMU.md`; not committed |
