# STATUS (WS-B, LiDAR -> scene)

Updated: 2026-11-07

## Works
- `contract/models.py` + generated `contract/scene.schema.json` (pushed to main).

## In progress
- M0: validator, geometry helpers, fixtures, negative fixtures (subagent).
- M3: iOS RoomPlan app scaffold and CapturedRoom JSON format (subagent).

## Blocked / needs a human
- Real scans need a LiDAR iPhone or iPad and a person to walk the building.
- iOS app install needs Xcode + Apple ID signing on a device.

## How to run
```
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```
