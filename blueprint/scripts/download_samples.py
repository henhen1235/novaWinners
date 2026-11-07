"""Fetch optional public plan samples into data/. Nothing here is committed.

CubiCasa5K, a public fire-evacuation plan, and a buildingSMART IFC sample.
A blocked or missing download is printed and the synthetic fixtures stay the demo.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

SOURCES = {
    "cubicasa": "https://github.com/CubiCasa/CubiCasa5k",
    "buildingsmart_ifc": "https://github.com/buildingSMART/Sample-Test-Files/raw/main/IFC%202x3/Duplex%20Apartment/Duplex_A_20110907.ifc",
    "evacuation_pdf": "https://www.nyc.gov/assets/fdny/downloads/pdf/business/cof-p-1001-sample-egress-plan.pdf",
}


def fetch(name: str, url: str) -> str:
    destination = DATA / name
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / Path(url.split("?")[0]).name
    if "github.com/CubiCasa/CubiCasa5k" in url and not url.endswith((".zip", ".png", ".pdf")):
        return f"{name}: dataset page only ({url}). Weights and images are not redistributed."
    try:
        urllib.request.urlretrieve(url, target)
    except Exception as exc:  # noqa: BLE001 — a blocked download is a status note, not a crash
        return f"{name}: download blocked ({exc.__class__.__name__}: {exc})"
    return f"{name}: saved {target.relative_to(ROOT)} ({target.stat().st_size} bytes)"


def main() -> None:
    DATA.mkdir(exist_ok=True)
    for name, url in SOURCES.items():
        print(fetch(name, url))


if __name__ == "__main__":
    main()
