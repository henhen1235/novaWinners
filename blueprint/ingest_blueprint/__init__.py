"""Blueprint ingestion. Pixel space is top-left, y down. Meters are XZ, Y up."""

import sys
from pathlib import Path

# The scene contract is the repo-root `contract` package (D18); blueprint code runs
# from blueprint/, so append the repo root. Appended, so blueprint's own modules win.
_REPO = str(Path(__file__).resolve().parents[2])
if _REPO not in sys.path:
    sys.path.append(_REPO)
