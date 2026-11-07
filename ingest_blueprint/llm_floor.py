"""Vision floor-plan reader.

The model returns one floor description: a building name, a floor label, a
printed scale, and axis-aligned rooms in normalized image coordinates.
This module turns that into meter geometry and a scene.json through
``assemble_scene``. It does not ask the model for scene.json.

Pixel y and meter z both increase downward. A printed scale of 1/S at 200 DPI
is ``px_per_m = (200/25.4) * (1000/S)``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Polygon

from export.assemble import assemble_scene
from geometry.doors import cut_walls_for_doors
from geometry.rooms import attach_doors, room_type_from_name
from ingest_blueprint.pipeline import generated_by, publish_scene
from ingest_blueprint.semantics import api_key, load_env, model_name
from ingest_blueprint.types import DoorOpening, Segment

SLAB_M = 3.0
WALL_M = 0.2
DOOR_M = 0.9
DPI = 200.0
ROOM_TYPES = {
    "office",
    "corridor",
    "stairwell",
    "lobby",
    "restroom",
    "classroom",
    "storage",
    "mechanical",
    "outside",
    "unknown",
}
CIRCULATION = {"corridor", "lobby", "stairwell"}
NAME_HINTS = (
    ("階段", "stairwell"),
    ("stair", "stairwell"),
    ("廊下", "corridor"),
    ("通路", "corridor"),
    ("corridor", "corridor"),
    ("ロビー", "lobby"),
    ("玄関", "lobby"),
    ("lobby", "lobby"),
    ("便所", "restroom"),
    ("トイレ", "restroom"),
    ("倉庫", "storage"),
    ("機械", "mechanical"),
    ("教室", "classroom"),
    ("事務", "office"),
    ("office", "office"),
)
FLOOR_PROMPT = (
    "You read one architectural floor-plan sheet. Return JSON only, no markdown. "
    "Ignore dimension lines, grid bubbles, the north arrow, the title block, and area tables. "
    "Trace rooms inside the exterior walls. "
    '{"building":"","floor":"","scale":"1/500",'
    '"rooms":[{"name":"","type":"corridor","x":0.1,"z":0.1,"w":0.2,"h":0.2}],'
    '"doors":[{"x":0.2,"z":0.3}],"exits":[{"x":0.1,"z":0.4,"label":"Exit"}],'
    '"stairs":[{"x":0.8,"z":0.2,"name":"Stair"}]}. '
    "Coordinates are normalized 0..1 from the top-left, x right, z down, rounded to 2 decimals. "
    "x,z is each room's top-left corner. Rooms that share a wall use the same edge. "
    "type is office, corridor, stairwell, lobby, restroom, classroom, storage, mechanical, outside, or unknown. "
    "Include corridors and stair halls. Put door points on openings, exit points on exterior doors, "
    "and stair points inside stair halls. Do not estimate meters."
)


class _ModelRejected(RuntimeError):
    """The configured model name was refused. Try one stronger vision model."""


@dataclass(frozen=True)
class PlanSheet:
    page_index: int
    title: str
    building: str
    floor_label: str
    scale: int | None
    kind: str


@dataclass
class RoomBox:
    name: str
    room_type: str
    x0: float
    z0: float
    x1: float
    z1: float
    stair: bool = False

    def contains(self, x: float, z: float) -> bool:
        return self.x0 - 0.05 <= x <= self.x1 + 0.05 and self.z0 - 0.05 <= z <= self.z1 + 0.05

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.z1 - self.z0)


@dataclass(frozen=True)
class WallRun:
    p1: tuple[float, float]
    p2: tuple[float, float]
    owners: tuple[int, ...]

    @property
    def length(self) -> float:
        return math.dist(self.p1, self.p2)

    @property
    def midpoint(self) -> tuple[float, float]:
        return ((self.p1[0] + self.p2[0]) / 2.0, (self.p1[1] + self.p2[1]) / 2.0)


def px_per_m_from_text(text: str | None, dpi: float = DPI) -> float | None:
    """Pixels per meter for a printed ratio such as ``1/500`` at ``dpi``."""

    denominator = _scale_denominator(text or "")
    if denominator is None:
        return None
    return (dpi / 25.4) * (1000.0 / denominator)


def parse_model_json(content: str) -> dict:
    """Parse a model reply, including one wrapped in a markdown fence."""

    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    if start < 0:
        return {}
    snippet = text[start:]
    end = snippet.rfind("}")
    if end >= 0:
        try:
            payload = json.loads(snippet[: end + 1])
        except json.JSONDecodeError:
            payload = _repair_json(snippet)
        else:
            return payload if isinstance(payload, dict) else {}
    else:
        payload = _repair_json(snippet)
    return payload if isinstance(payload, dict) else {}


def _repair_json(snippet: str) -> dict:
    """Close a floor JSON object that ran out of output tokens mid-list."""

    for index in range(len(snippet) - 1, 0, -1):
        if snippet[index] not in "}]":
            continue
        candidate = snippet[: index + 1]
        extra = max(0, candidate.count("[") - candidate.count("]"))
        extra_obj = max(0, candidate.count("{") - candidate.count("}"))
        if candidate.count("]") > candidate.count("[") or candidate.count("}") > candidate.count("{"):
            continue
        candidate = candidate + ("]" * extra) + ("}" * extra_obj)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("rooms"):
            return payload
    return {}


def expand_floor_labels(label: str) -> list[tuple[float, str]]:
    """Expand a sheet title into stacked floors. ``4～12階`` becomes 4F..12F."""

    text = _normalize(label)
    span = re.search(r"(\d+)\s*[~\-]\s*(\d+)\s*階", text)
    if span:
        start, end = int(span.group(1)), int(span.group(2))
        if start > end:
            start, end = end, start
        if 0 < end - start <= 40:
            return [(float(number), f"{number}F") for number in range(start, end + 1)]
    basement = re.search(r"(?:地下|B)\s*(\d+)\s*階", text)
    if basement:
        number = int(basement.group(1))
        return [(-float(number), f"B{number}")]
    if re.search(r"R\s*階|屋上|屋根階", text):
        return [(10000.0, "RF")]
    single = re.search(r"(\d+)\s*階", text)
    if single:
        return [(float(single.group(1)), f"{single.group(1)}F")]
    cleaned = text.strip()
    return [(0.0, cleaned[:40] or "Floor")]


def classify_sheet(page_index: int, text: str) -> PlanSheet:
    """Classify one page from its text layer. Floor plans stay; sections do not."""

    title = _title_text(text)
    building = _building_key(title)
    return PlanSheet(
        page_index=page_index,
        title=title,
        building=building,
        floor_label=title,
        scale=_scale_denominator(title),
        kind=_sheet_kind(title),
    )


def choose_building(sheets: list[PlanSheet]) -> list[PlanSheet]:
    """Keep the building with the most floor-plan sheets.

    Room details drawn at a finer scale than the rest of that building
    (the 1/100 guest-room sheet) are dropped. Sections, details, site plans,
    and area tables never enter ``sheets`` as floors.
    """

    groups: dict[str, list[PlanSheet]] = defaultdict(list)
    for sheet in sheets:
        if sheet.kind == "floor":
            groups[sheet.building].append(sheet)
    best: list[PlanSheet] = []
    best_rank: tuple[int, int, int] | None = None
    for items in groups.values():
        filtered = _drop_detail_scales(items)
        if not filtered:
            continue
        expanded = 0
        for sheet in filtered:
            expanded += max(1, len(expand_floor_labels(sheet.floor_label)))
        rank = (len(filtered), expanded, -min(sheet.page_index for sheet in filtered))
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = filtered
    return sorted(best, key=lambda sheet: sheet.page_index)


# Short English names for the MLIT model-building sheets. Unknown titles stay untitled
# in English rather than guessing a translation.
_KNOWN_BUILDINGS = {
    "事務所": ("office", "Office"),
    "ビジネスホテル": ("business-hotel", "Business hotel"),
    "シティホテル": ("city-hotel", "City hotel"),
    "総合病院": ("hospital", "General hospital"),
    "クリニック": ("clinic", "Clinic"),
    "福祉施設": ("welfare", "Welfare facility"),
    "大規模物販店舗": ("large-shop", "Large shop"),
    "小規模物販店舗": ("small-shop", "Small shop"),
    "学校": ("school", "School"),
    "幼稚園": ("kindergarten", "Kindergarten"),
    "大学_高層棟": ("university-high", "University high-rise"),
    "大学_低層棟": ("university-low", "University low-rise"),
    "学校等・講堂": ("auditorium", "Auditorium"),
    "飲食店": ("restaurant", "Restaurant"),
}


def group_buildings(sheets: list[PlanSheet]) -> list[dict]:
    """Group floor-plan sheets by building, in first-seen order.

    Sections, details, site plans, and height tables never have kind ``floor``.
    A finer scale than the rest of a building (the 1/100 guest-room sheet) is
    dropped the same way ``choose_building`` drops it. No model call.
    """

    order: list[str] = []
    groups: dict[str, list[PlanSheet]] = {}
    for sheet in sheets:
        if sheet.kind != "floor":
            continue
        groups.setdefault(sheet.building, []).append(sheet)
        if sheet.building not in order:
            order.append(sheet.building)
    buildings: list[dict] = []
    for key in order:
        filtered = _drop_detail_scales(groups[key])
        if not filtered:
            continue
        name = _short_building(key)
        slug, gloss = _KNOWN_BUILDINGS.get(name, ("", ""))
        if not slug:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or f"building-{len(buildings) + 1}"
        floors: list[str] = []
        sheet_rows: list[dict] = []
        for sheet in sorted(filtered, key=lambda item: item.page_index):
            labels = [label for _elevation, label in expand_floor_labels(sheet.floor_label)]
            floors.extend(labels)
            sheet_rows.append({"page": sheet.page_index + 1, "title": sheet.title, "floors": labels})
        buildings.append(
            {
                "name": name,
                "key": key,
                "slug": slug,
                "gloss": gloss,
                "sheets": sheet_rows,
                "floors": floors,
            }
        )
    return buildings


def list_buildings(source: str | Path) -> list[dict]:
    """Read building names from a PDF text layer. No model call."""

    path = Path(source)
    if path.suffix.lower() != ".pdf":
        return []
    import fitz

    document = fitz.open(path)
    try:
        sheets = [classify_sheet(index, page.get_text("text")) for index, page in enumerate(document)]
    finally:
        document.close()
    return group_buildings(sheets)


def scene_from_description(
    description: dict,
    width_px: int,
    height_px: int,
    floor_label: str = "",
    dpi: float = DPI,
) -> dict:
    """Build a scene from one floor description, duplicating a typical-floor range."""

    template = level_from_description(description, width_px, height_px, dpi=dpi)
    if template is None:
        raise ValueError("floor description has no usable rooms")
    spans = _spans(floor_label, str(description.get("floor") or ""))
    levels = _stack([(key, 0, name, template) for key, name in spans])
    scene, _walkable = assemble_scene(
        levels,
        meta_name=str(description.get("building") or "Building"),
        source_file="llm-floor",
        scale_method="dimension_text",
        scale_confidence=0.8,
        generated_by=generated_by(),
        source="blueprint",
    )
    return scene


def level_from_description(
    description: dict,
    width_px: int,
    height_px: int,
    dpi: float = DPI,
    sheet_scale: int | None = None,
) -> dict | None:
    """Convert one floor description into an assembler level template."""

    ppm = _px_per_m(description, sheet_scale, dpi)
    if ppm is None or width_px <= 0 or height_px <= 0:
        return None
    rooms = _room_boxes(description, width_px, height_px, ppm)
    if not rooms:
        return None
    _mark_stairs(rooms, _points(description.get("stairs"), width_px, height_px, ppm))
    walls, doors = _walls_and_doors(
        rooms,
        _points(description.get("doors"), width_px, height_px, ppm),
        _exit_points(description.get("exits"), width_px, height_px, ppm),
    )
    faces = [
        Polygon([(room.x0, room.z0), (room.x1, room.z0), (room.x1, room.z1), (room.x0, room.z1)])
        for room in rooms
    ]
    labels = [(room.name, "stairwell" if room.stair else room.room_type) for room in rooms]
    stair_indexes = {index for index, room in enumerate(rooms) if room.stair or room.room_type == "stairwell"}
    attached = attach_doors(doors, faces)
    kept: list[DoorOpening] = []
    for door, hits in attached:
        door.hits = hits
        if hits:
            kept.append(door)
    if not kept:
        return None
    return {
        "walls": walls,
        "doors": kept,
        "faces": faces,
        "labels": labels,
        "obstacles": [],
        "stair_indexes": stair_indexes,
        "px_per_m": ppm,
        "ceiling_height_m": 2.7,
    }


def read_building(source: str | Path, out_dir: str | Path) -> dict | None:
    """Read one building from a blueprint. ``None`` when the model is unavailable."""

    path = Path(source)
    out = Path(out_dir)
    if path.suffix.lower() == ".dxf":
        return None
    sheets = _sheets_for(path)
    if not sheets:
        return None
    load_env()
    if not api_key():
        return None
    parsed: list[tuple[PlanSheet, dict, dict, np.ndarray]] = []
    document = None
    if path.suffix.lower() == ".pdf":
        import fitz

        document = fitz.open(path)
    try:
        for sheet in sheets:
            raster = _raster(path, sheet, document)
            print(f"reading floor sheet {sheet.page_index + 1}", file=sys.stderr, flush=True)
            try:
                description = _describe(raster, sheet, out / "llm_cache")
            except Exception as exc:
                _log_sheet_failure(sheet.page_index, exc)
                continue
            if not isinstance(description, dict):
                continue
            template = level_from_description(
                description,
                raster.shape[1],
                raster.shape[0],
                dpi=DPI,
                sheet_scale=sheet.scale,
            )
            if template is None:
                continue
            parsed.append((sheet, description, template, raster))
    finally:
        if document is not None:
            document.close()
    if not parsed:
        return None
    entries = []
    for sheet, description, template, raster in parsed:
        for key, name in _spans(sheet.floor_label, str(description.get("floor") or "")):
            entries.append((key, sheet.page_index, name, template, raster))
    entries.sort(key=lambda item: (item[0], item[1], item[2]))
    levels = _stack([(key, page, name, template) for key, page, name, template, _raster in entries])
    building = _display_building(parsed)
    scene, _walkable = assemble_scene(
        levels,
        meta_name=building,
        source_file=str(path),
        scale_method="dimension_text",
        scale_confidence=0.8,
        generated_by=generated_by(),
        source="blueprint",
    )
    overlay = min(entries, key=lambda item: (abs(item[0] - 1.0), item[0] < 0, item[1]))
    _write_overlay(out / "overlay.png", overlay[4], overlay[3])
    scene, code = publish_scene(scene, out)
    return {
        "scene": scene,
        "exit_code": code,
        "building": building,
        "floors": len(levels),
        "reader": "llm",
        "pages": [sheet.page_index for sheet, _description, _template, _raster in parsed],
    }


def _describe(raster: np.ndarray, sheet: PlanSheet, cache_dir: Path) -> dict:
    prompt = FLOOR_PROMPT
    if sheet.title:
        prompt = f"{FLOOR_PROMPT}\nSheet title: {sheet.title}"
    image = _png(raster)
    model = model_name()
    digest = hashlib.sha256(model.encode() + b"\0" + prompt.encode() + b"\0" + image).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{digest}.json"
    if cache_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    payload = _call_openrouter(model, api_key() or "", prompt, image)
    if isinstance(payload, dict) and "rooms" in payload:
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def _log_sheet_failure(page_index: int, exc: Exception) -> None:
    message = str(exc)
    if "sk-or" in message or "Bearer" in message or "OPENROUTER_API_KEY" in message:
        message = "request failed"
    print(f"floor sheet {page_index + 1} failed: {type(exc).__name__}: {message[:240]}", file=sys.stderr, flush=True)


def _call_openrouter(model: str, key: str, prompt: str, image: bytes) -> dict:
    return _post_model(model, key, prompt, image)


def _post_model(model: str, key: str, prompt: str, image: bytes) -> dict:
    import httpx

    data_url = "data:image/png;base64," + base64.b64encode(image).decode()
    max_tokens = 1200
    response = None
    for _attempt in range(3):
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0,
                "max_tokens": max_tokens,
                "reasoning": {"effort": "minimal"},
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
            },
            timeout=120.0,
        )
        if response.status_code != 402:
            break
        affordable = _affordable_tokens(response.text)
        if affordable is None or affordable >= max_tokens or affordable < 280:
            break
        max_tokens = affordable
    if response is None:
        raise RuntimeError("model request was not sent")
    if response.status_code in {400, 404, 422} and _rejects_model(response):
        raise _ModelRejected(f"model {model} was rejected ({response.status_code})")
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]["content"]
    if isinstance(message, list):
        message = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in message)
    return parse_model_json(str(message or ""))


def _affordable_tokens(body: str) -> int | None:
    match = re.search(r"afford (\d+)", body)
    if not match:
        return None
    return int(match.group(1))


def _rejects_model(response) -> bool:
    if response.status_code == 404:
        return True
    body = response.text.lower()
    return "model" in body and ("not found" in body or "invalid" in body or "unavailable" in body)


def _sheets_for(path: Path) -> list[PlanSheet]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import fitz

        document = fitz.open(path)
        try:
            sheets = [classify_sheet(index, page.get_text("text")) for index, page in enumerate(document)]
        finally:
            document.close()
        chosen = choose_building(sheets)
        if chosen:
            return chosen
        if len(sheets) == 1:
            only = sheets[0]
            return [PlanSheet(only.page_index, only.title, only.building or "Building", only.floor_label, only.scale, "floor")]
        return []
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return [PlanSheet(0, "", "Building", "", None, "floor")]
    return []


def _raster(path: Path, sheet: PlanSheet, document) -> np.ndarray:
    if document is not None:
        import fitz

        page = document[sheet.page_index]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(DPI / 72.0, DPI / 72.0), alpha=False)
        raster = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
        if pixmap.n == 4:
            raster = raster[:, :, :3]
        elif pixmap.n == 1:
            raster = np.repeat(raster, 3, axis=2)
        return np.ascontiguousarray(raster)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _png(raster: np.ndarray) -> bytes:
    image = raster
    height, width = raster.shape[:2]
    longest = max(height, width)
    if longest > 1200:
        scale = 1200.0 / longest
        image = cv2.resize(raster, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError("could not encode the floor sheet")
    return encoded.tobytes()


def _px_per_m(description: dict, sheet_scale: int | None, dpi: float) -> float | None:
    if sheet_scale:
        return (dpi / 25.4) * (1000.0 / float(sheet_scale))
    return px_per_m_from_text(str(description.get("scale") or ""), dpi)


def _room_boxes(description: dict, width_px: int, height_px: int, ppm: float) -> list[RoomBox]:
    rooms: list[RoomBox] = []
    for item in description.get("rooms") or []:
        if not isinstance(item, dict):
            continue
        rect = _rectangle(item)
        if rect is None:
            continue
        x, z, w, h = rect
        x0, x1 = sorted((x * width_px / ppm, (x + w) * width_px / ppm))
        z0, z1 = sorted((z * height_px / ppm, (z + h) * height_px / ppm))
        name = str(item.get("name") or "Room").strip() or "Room"
        room_type = _room_type(name, str(item.get("type") or "unknown"))
        if room_type == "outside":
            continue
        rooms.append(RoomBox(name[:80], room_type, x0, z0, x1, z1))
    rooms = _snap_rooms(rooms)
    rooms = [room for room in rooms if room.x1 - room.x0 >= 0.6 and room.z1 - room.z0 >= 0.6 and room.area >= 0.8]
    rooms = _drop_containers(rooms)
    unique: list[RoomBox] = []
    seen: set[tuple[float, float, float, float]] = set()
    for room in rooms:
        key = (round(room.x0, 3), round(room.z0, 3), round(room.x1, 3), round(room.z1, 3))
        if key in seen:
            continue
        seen.add(key)
        unique.append(room)
    return unique


def _rectangle(item: dict) -> tuple[float, float, float, float] | None:
    if "x1" in item and ("z1" in item or "y1" in item) and "w" not in item and "width" not in item:
        try:
            x0 = float(item.get("x0", item.get("x", 0)))
            z0 = float(item.get("z0", item.get("z", item.get("y0", item.get("y", 0)))))
            x1 = float(item["x1"])
            z1 = float(item.get("z1", item.get("y1")))
        except (TypeError, ValueError):
            return None
        x0, z0, x1, z1 = _maybe_percent(x0, z0, x1, z1)
        if x0 > x1:
            x0, x1 = x1, x0
        if z0 > z1:
            z0, z1 = z1, z0
        return x0, z0, x1 - x0, z1 - z0
    try:
        x = float(item.get("x", item.get("nx", item.get("left", 0))))
        z = float(item.get("z", item.get("nz", item.get("y", item.get("top", 0)))))
        w = float(item.get("w", item.get("width", item.get("nw", 0))))
        h = float(item.get("h", item.get("height", item.get("nh", item.get("d", 0)))))
    except (TypeError, ValueError):
        return None
    x, z, w, h = _maybe_percent(x, z, w, h)
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        z, h = z + h, -h
    if w <= 0.002 or h <= 0.002:
        return None
    return x, z, w, h


def _maybe_percent(*values: float) -> tuple[float, ...]:
    if max(abs(value) for value in values) > 1.5:
        return tuple(value / 100.0 for value in values)
    return values


def _room_type(name: str, declared: str) -> str:
    lowered = name.lower()
    if "階段" in name or "stair" in lowered:
        return "stairwell"
    if declared in ROOM_TYPES and declared != "unknown":
        return declared
    for token, room_type in NAME_HINTS:
        if token in name or token in lowered:
            return room_type
    guessed = room_type_from_name(name)
    return guessed if guessed in ROOM_TYPES else "unknown"


def _snap_rooms(rooms: list[RoomBox], tolerance: float = 0.5) -> list[RoomBox]:
    if not rooms:
        return []
    xs = _cluster([coord for room in rooms for coord in (room.x0, room.x1)], tolerance)
    zs = _cluster([coord for room in rooms for coord in (room.z0, room.z1)], tolerance)
    snapped: list[RoomBox] = []
    for room in rooms:
        x0, x1 = sorted((xs[round(room.x0, 4)], xs[round(room.x1, 4)]))
        z0, z1 = sorted((zs[round(room.z0, 4)], zs[round(room.z1, 4)]))
        snapped.append(RoomBox(room.name, room.room_type, x0, z0, x1, z1, room.stair))
    return snapped


def _cluster(values: list[float], tolerance: float) -> dict[float, float]:
    ordered = sorted(set(round(value, 4) for value in values))
    groups: list[list[float]] = []
    for value in ordered:
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    mapping: dict[float, float] = {}
    for group in groups:
        mean = round(sum(group) / len(group), 4)
        for value in group:
            mapping[value] = mean
    return mapping


def _drop_containers(rooms: list[RoomBox]) -> list[RoomBox]:
    if len(rooms) < 2:
        return rooms
    largest = max(rooms, key=lambda room: room.area)
    others = [room for room in rooms if room is not largest]
    contained = 0
    for room in others:
        center_x = (room.x0 + room.x1) / 2.0
        center_z = (room.z0 + room.z1) / 2.0
        if largest.x0 <= center_x <= largest.x1 and largest.z0 <= center_z <= largest.z1:
            contained += 1
    if contained >= max(1, int(0.6 * len(others))) and largest.area > 1.5 * max(room.area for room in others):
        return others
    return rooms


def _points(items, width_px: int, height_px: int, ppm: float) -> list[tuple[float, float]]:
    points = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            x = float(item.get("x", item.get("nx")))
            z = float(item.get("z", item.get("nz", item.get("y"))))
        except (TypeError, ValueError):
            continue
        x, z = _maybe_percent(x, z)
        points.append((x * width_px / ppm, z * height_px / ppm))
    return points


def _exit_points(items, width_px: int, height_px: int, ppm: float) -> list[tuple[tuple[float, float], str]]:
    exits = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            x = float(item.get("x", item.get("nx")))
            z = float(item.get("z", item.get("nz", item.get("y"))))
        except (TypeError, ValueError):
            continue
        x, z = _maybe_percent(x, z)
        label = str(item.get("label") or item.get("name") or "Exit").strip() or "Exit"
        exits.append(((x * width_px / ppm, z * height_px / ppm), label[:80]))
    return exits


def _mark_stairs(rooms: list[RoomBox], points: list[tuple[float, float]]) -> None:
    for room in rooms:
        if room.room_type == "stairwell":
            room.stair = True
    for point in points:
        for room in rooms:
            if room.contains(point[0], point[1]):
                room.stair = True
                room.room_type = "stairwell"
                break


def _walls_and_doors(rooms, door_points, exit_points):
    runs = _wall_runs(rooms)
    doors: list[DoorOpening] = []
    span = _span(rooms)
    tolerance = max(1.5, span * 0.03)
    circulation = {index for index, room in enumerate(rooms) if room.room_type in CIRCULATION or room.stair}
    for point in door_points:
        run = _nearest_run(runs, point, tolerance, exterior_only=False)
        if run is not None:
            _add_door(doors, run, point, False, "Door")
    for run in runs:
        if len(run.owners) < 2 or run.length < 0.8:
            continue
        if circulation and not any(owner in circulation for owner in run.owners):
            continue
        _add_door(doors, run, run.midpoint, False, "Door")
    for point, label in exit_points:
        exterior = [run for run in runs if len(run.owners) == 1]
        run = _nearest_run(exterior or runs, point, tolerance, exterior_only=False)
        if run is not None:
            _add_door(doors, run, point, True, label)
    if not any(door.is_exit for door in doors):
        exterior = [run for run in runs if len(run.owners) == 1 and run.length >= 0.9]
        if exterior:
            longest = max(exterior, key=lambda run: run.length)
            _add_door(doors, longest, longest.midpoint, True, "Exit")
    walls = [Segment(_q(run.p1), _q(run.p2), WALL_M) for run in runs if run.length >= 0.05]
    walls = cut_walls_for_doors(walls, doors)
    return walls, doors


def _wall_runs(rooms: list[RoomBox]) -> list[WallRun]:
    vertical: dict[float, list[tuple[float, float, int]]] = defaultdict(list)
    horizontal: dict[float, list[tuple[float, float, int]]] = defaultdict(list)
    for index, room in enumerate(rooms):
        x0, x1 = round(room.x0, 4), round(room.x1, 4)
        z0, z1 = round(room.z0, 4), round(room.z1, 4)
        vertical[x0].append((z0, z1, index))
        vertical[x1].append((z0, z1, index))
        horizontal[z0].append((x0, x1, index))
        horizontal[z1].append((x0, x1, index))
    runs: list[WallRun] = []
    for fixed, intervals in vertical.items():
        runs.extend(_merge_runs(fixed, intervals, vertical=True))
    for fixed, intervals in horizontal.items():
        runs.extend(_merge_runs(fixed, intervals, vertical=False))
    return runs


def _merge_runs(fixed: float, intervals: list[tuple[float, float, int]], vertical: bool) -> list[WallRun]:
    coords = sorted({round(value, 4) for start, stop, _index in intervals for value in (start, stop)})
    pieces: list[WallRun] = []
    for left, right in zip(coords, coords[1:]):
        if right - left < 0.05:
            continue
        mid = (left + right) / 2.0
        owners = tuple(sorted({index for start, end, index in intervals if start - 1e-4 <= mid <= end + 1e-4}))
        if not owners:
            continue
        if vertical:
            run = WallRun((fixed, left), (fixed, right), owners)
        else:
            run = WallRun((left, fixed), (right, fixed), owners)
        if pieces and pieces[-1].owners == run.owners and math.dist(pieces[-1].p2, run.p1) < 1e-3:
            pieces[-1] = WallRun(pieces[-1].p1, run.p2, run.owners)
        else:
            pieces.append(run)
    return pieces


def _nearest_run(runs: list[WallRun], point: tuple[float, float], tolerance: float, exterior_only: bool) -> WallRun | None:
    best: WallRun | None = None
    best_distance = tolerance
    for run in runs:
        if exterior_only and len(run.owners) != 1:
            continue
        distance, _projection = _project(point, run.p1, run.p2)
        if distance <= best_distance:
            best = run
            best_distance = distance
    return best


def _add_door(doors: list[DoorOpening], run: WallRun, point: tuple[float, float], is_exit: bool, label: str) -> None:
    opening = _opening(run, point)
    if opening is None:
        return
    p1, p2, width = opening
    midpoint = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    for door in doors:
        if math.dist(midpoint, door.midpoint) < 0.8:
            if is_exit:
                door.is_exit = True
                door.label = label or door.label
            return
    doors.append(DoorOpening(_q(p1), _q(p2), width, "swing", is_exit, "outside", label or ("Exit" if is_exit else "Door")))


def _opening(run: WallRun, point: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float], float] | None:
    length = run.length
    if length < 0.7:
        return None
    _distance, center = _project(point, run.p1, run.p2)
    direction = _unit(run.p1, run.p2)
    along = (center[0] - run.p1[0]) * direction[0] + (center[1] - run.p1[1]) * direction[1]
    width = DOOR_M
    if length < width + 0.08:
        width = max(0.6, length - 0.08)
    if width > 3.0:
        width = 2.4
    half = width / 2.0
    along = min(max(along, half + 0.01), length - half - 0.01)
    start = (run.p1[0] + direction[0] * (along - half), run.p1[1] + direction[1] * (along - half))
    end = (run.p1[0] + direction[0] * (along + half), run.p1[1] + direction[1] * (along + half))
    return start, end, round(math.dist(start, end), 4)


def _stack(entries: list[tuple[float, int, str, dict]]) -> list[dict]:
    ordered = sorted(entries, key=lambda item: (item[0], item[1], item[2]))
    return [_clone_level(template, f"L{index + 1}", name, index * SLAB_M) for index, (_key, _page, name, template) in enumerate(ordered)]


def _clone_level(template: dict, level_id: str, name: str, elevation: float) -> dict:
    return {
        "level_id": level_id,
        "name": name,
        "elevation_m": elevation,
        "ceiling_height_m": template["ceiling_height_m"],
        "walls": [Segment(tuple(wall.p1), tuple(wall.p2), wall.thickness_m) for wall in template["walls"]],
        "doors": [
            DoorOpening(
                tuple(door.p1),
                tuple(door.p2),
                door.width_m,
                door.kind,
                door.is_exit,
                door.leads_to,
                door.label,
                list(door.hits),
            )
            for door in template["doors"]
        ],
        "faces": [Polygon(list(face.exterior.coords)) for face in template["faces"]],
        "labels": list(template["labels"]),
        "obstacles": [],
        "stair_indexes": set(template["stair_indexes"]),
    }


def _spans(primary: str, secondary: str) -> list[tuple[float, str]]:
    for label in (primary, secondary):
        if label and _has_floor_token(label):
            return expand_floor_labels(label)
    for label in (secondary, primary):
        if label and label.strip():
            return [(0.0, label.strip()[:40])]
    return [(0.0, "Floor")]


def _has_floor_token(label: str) -> bool:
    text = _normalize(label)
    return re.search(r"\d+\s*階|R\s*階|屋上|地下", text) is not None


def _display_building(parsed: list[tuple[PlanSheet, dict, dict, np.ndarray]]) -> str:
    for sheet, _description, _template, _raster in parsed:
        if sheet.building and sheet.building != "Building":
            return _short_building(sheet.building)
    for _sheet, description, _template, _raster in parsed:
        name = str(description.get("building") or "").strip()
        if name:
            return name[:80]
    return "Building"


def _write_overlay(path: Path, raster: np.ndarray, template: dict) -> None:
    image = raster.copy()
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    ppm = float(template["px_per_m"])

    def px(point) -> tuple[int, int]:
        return (int(round(point[0] * ppm)), int(round(point[1] * ppm)))

    for face in template["faces"]:
        pts = np.array([px(point) for point in face.exterior.coords], dtype=np.int32)
        cv2.polylines(image, [pts], True, (40, 90, 220), 2)
    for door in template["doors"]:
        color = (30, 160, 70) if door.is_exit else (220, 60, 40)
        cv2.line(image, px(door.p1), px(door.p2), color, 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))


def _title_text(text: str) -> str:
    lines = [_normalize(line.strip()) for line in text.splitlines() if line.strip()]
    picked = [line for line in lines if any(token in line for token in ("図", "モデル", "階高", "配置"))]
    return " ".join(picked) if picked else " ".join(lines[:6])


def _building_key(title: str) -> str:
    match = re.search(r"モデル建物\(([^)]*)\)", title)
    if not match:
        return "Building"
    return match.group(1).strip() or "Building"


def _short_building(key: str) -> str:
    head = re.split(r"[、,]", key, maxsplit=1)[0].strip()
    return head or key


def _sheet_kind(title: str) -> str:
    if "平面図" not in title:
        return "skip"
    if any(token in title for token in ("断面図", "詳細", "配置図", "階高")):
        return "skip"
    return "floor"


def _scale_denominator(text: str) -> int | None:
    normalized = _normalize(text)
    match = re.search(r"1\s*/\s*(\d{2,4})", normalized)
    if not match:
        return None
    value = int(match.group(1))
    if 20 <= value <= 2000:
        return value
    return None


def _drop_detail_scales(sheets: list[PlanSheet]) -> list[PlanSheet]:
    denominators = [sheet.scale for sheet in sheets if sheet.scale]
    if not denominators:
        return list(sheets)
    counts = Counter(denominators)
    best_count = max(counts.values())
    chosen = max(scale for scale, count in counts.items() if count == best_count)
    return [sheet for sheet in sheets if sheet.scale is None or sheet.scale == chosen]


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return normalized.replace("〜", "~").replace("～", "~")


def _span(rooms: list[RoomBox]) -> float:
    xs = [coord for room in rooms for coord in (room.x0, room.x1)]
    zs = [coord for room in rooms for coord in (room.z0, room.z1)]
    return max(max(xs) - min(xs), max(zs) - min(zs))


def _project(point, start, end) -> tuple[float, tuple[float, float]]:
    dx = end[0] - start[0]
    dz = end[1] - start[1]
    length2 = dx * dx + dz * dz
    if length2 < 1e-12:
        return math.dist(point, start), start
    tee = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dz) / length2
    tee = min(1.0, max(0.0, tee))
    projection = (start[0] + dx * tee, start[1] + dz * tee)
    return math.dist(point, projection), projection


def _unit(start, end) -> tuple[float, float]:
    length = math.dist(start, end) or 1.0
    return ((end[0] - start[0]) / length, (end[1] - start[1]) / length)


def _q(point: tuple[float, float]) -> tuple[float, float]:
    return (round(point[0], 4), round(point[1], 4))
