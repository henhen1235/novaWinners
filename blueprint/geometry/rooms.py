"""Room faces from wall centerlines plus door segments.

``polygonize`` returns the bounded faces. The face outside the building is not
produced. A door with one adjacent face connects that room to ``outside``.
"""

from __future__ import annotations

import re

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from ingest_blueprint.types import DoorOpening, Segment

ROOM_KEYWORDS = (
    ("corridor", "corridor"),
    ("hall", "corridor"),
    ("stair", "stairwell"),
    ("lobby", "lobby"),
    ("restroom", "restroom"),
    ("toilet", "restroom"),
    ("wash", "restroom"),
    ("class", "classroom"),
    ("office", "office"),
    ("annex", "office"),
    ("storage", "storage"),
    ("closet", "storage"),
    ("mech", "mechanical"),
)


def _q(point: tuple[float, float]) -> tuple[float, float]:
    # 0.1 mm grid. PDF rounding otherwise leaves T-junctions a few ulps apart,
    # and polygonize will not close the room.
    return (round(point[0], 4), round(point[1], 4))


def faces_from_walls(walls: list[Segment], doors: list[DoorOpening], min_area_m2: float = 0.05) -> list[Polygon]:
    lines = [LineString([_q(wall.p1), _q(wall.p2)]) for wall in walls if wall.length_m >= 0.05]
    lines.extend(LineString([_q(door.p1), _q(door.p2)]) for door in doors if math_dist(door.p1, door.p2) >= 0.2)
    if not lines:
        return []
    noded = unary_union(lines)
    faces = []
    for face in polygonize(noded):
        if face.area >= min_area_m2 and face.is_valid:
            faces.append(face)
    return faces


def attach_doors(doors: list[DoorOpening], faces: list[Polygon]) -> list[tuple[DoorOpening, list[int]]]:
    """Return each door with the indexes of the faces that share its opening."""

    attached = []
    for door in doors:
        line = LineString([door.p1, door.p2])
        midpoint = line.interpolate(0.5)
        scored = []
        for index, face in enumerate(faces):
            boundary_distance = face.boundary.distance(midpoint)
            if boundary_distance <= 0.35:
                scored.append((boundary_distance, index))
        scored.sort()
        hits = [index for _, index in scored[:2]]
        attached.append((door, hits))
    return attached


def room_type_from_name(name: str) -> str:
    lowered = name.lower()
    for needle, room_type in ROOM_KEYWORDS:
        if needle in lowered:
            return room_type
    return "unknown"


def label_faces(faces: list[Polygon], texts: list[tuple[str, tuple[float, float], float]]) -> list[tuple[str, str]]:
    """Pick a name and type for each face from meter-space text anchors."""

    labels = []
    for face in faces:
        hits = []
        for text, anchor, size in texts:
            if _skip_text(text):
                continue
            if face.covers(Point(anchor)):
                hits.append((size, text))
        numbers = [(size, text) for text, anchor, size in texts if _ROOM_NUMBER.match(text.strip()) and face.covers(Point(anchor))]
        if not hits and not numbers:
            labels.append(("Room", "unknown"))
            continue
        hits.sort(reverse=True)
        numbers.sort(reverse=True)
        words = hits[0][1] if hits else ""
        number = numbers[0][1].strip() if numbers else ""
        name = f"{number} {words}".strip() if words != number else words
        labels.append((name, room_type_from_name(words or name)))
    return labels


# Room numbers on CAD sheets: "3404", "1001A". Pure digits are otherwise skipped as dimensions.
_ROOM_NUMBER = re.compile(r"^\d{3,5}[A-Z]?$")
_AREA_TAG = re.compile(r"^[\d,.]+\s*(sf|sq\.?\s*ft|ft2|m2|sqm)$", re.IGNORECASE)


def _skip_text(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped:
        return True
    if stripped.endswith("m") and any(character.isdigit() for character in stripped):
        return True
    if stripped.replace(" ", "").isdigit():
        return True
    if stripped in {"up", "dn", "down"}:
        return True
    if _AREA_TAG.match(stripped) or _ROOM_NUMBER.match(text.strip()):
        return True
    return False


def math_dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
