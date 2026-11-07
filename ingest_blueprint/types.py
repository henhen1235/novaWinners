"""Shared page and geometry records.

Pixel coordinates have their origin at the top-left of the page, y down.
Meter coordinates are floor-plan (x, z) with z increasing in the image's +y direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Stroke:
    points: list[tuple[float, float]]
    width_px: float
    color: tuple[float, float, float] | None
    is_curve: bool


@dataclass
class TextSpan:
    text: str
    bbox: tuple[float, float, float, float]
    size: float

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


@dataclass
class PlanPage:
    source_path: str
    page_index: int
    width_px: int
    height_px: int
    raster: np.ndarray
    strokes: list[Stroke]
    texts: list[TextSpan]
    dpi: float = 200.0


@dataclass
class ScaleEstimate:
    px_per_m: float
    method: str
    confidence: float


@dataclass
class Segment:
    """Wall centerline in meters on the XZ plane."""

    p1: tuple[float, float]
    p2: tuple[float, float]
    thickness_m: float = 0.2
    # None means a full-height wall. Railings set a shorter barrier height.
    height_m: float | None = None

    @property
    def length_m(self) -> float:
        dx = self.p2[0] - self.p1[0]
        dz = self.p2[1] - self.p1[1]
        return (dx * dx + dz * dz) ** 0.5


@dataclass
class DoorOpening:
    p1: tuple[float, float]
    p2: tuple[float, float]
    width_m: float
    kind: str = "swing"
    is_exit: bool = False
    leads_to: str = "outside"
    label: str = "Exit"
    hits: list[int] = field(default_factory=list)
    swing: int = 0

    @property
    def midpoint(self) -> tuple[float, float]:
        return ((self.p1[0] + self.p2[0]) / 2.0, (self.p1[1] + self.p2[1]) / 2.0)


@dataclass
class LevelGeometry:
    """Detected geometry for one sheet, in meters."""

    page: PlanPage
    scale: ScaleEstimate
    walls: list[Segment] = field(default_factory=list)
    doors: list[DoorOpening] = field(default_factory=list)
    rooms: list[dict] = field(default_factory=list)
    obstacles: list[dict] = field(default_factory=list)
    stairs: list[dict] = field(default_factory=list)
    labels: list[dict] = field(default_factory=list)
