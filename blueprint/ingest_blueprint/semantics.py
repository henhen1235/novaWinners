"""Room labels from vector text, with an optional vision call for names only.

The model is asked for normalized 0..1 anchors. Those anchors are snapped to a
detected room or door later. They are never used as measurements.
Calls are cached by SHA-256 of (model, prompt, image bytes).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv

PROMPT = (
    "You label a building floor plan. Return JSON only, no markdown. "
    "Fields: rooms (list of {name, type, nx, nz}), exits (list of {label, nx, nz}), "
    "stairs (list of {text, nx, nz}), scale_text (string). "
    "nx and nz are normalized 0..1 from the top-left of the image. "
    "type is one of office, corridor, stairwell, lobby, restroom, classroom, "
    "storage, mechanical, outside, unknown. Do not estimate meters."
)


def load_env() -> None:
    load_dotenv()


def model_name() -> str:
    return os.environ.get("OPENROUTER_MODEL") or os.environ.get("ANTHROPIC_MODEL") or "anthropic/claude-sonnet-4.5"


def api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")


def label_image(raster: np.ndarray, cache_dir: Path, no_llm: bool) -> list[dict]:
    """Return label dicts with pixel ``anchor`` (x, y) and ``name`` / ``type`` / ``kind``."""

    if no_llm:
        return []
    load_env()
    key = api_key()
    if not key:
        return []
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(raster, cv2.COLOR_RGB2BGR))
    if not ok:
        return []
    image = encoded.tobytes()
    model = model_name()
    digest = hashlib.sha256(model.encode() + b"\0" + PROMPT.encode() + b"\0" + image).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{digest}.json"
    if cache_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        payload = _call_openrouter(model, key, image)
        cache_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return _to_pixels(payload, raster.shape[1], raster.shape[0])


def _call_openrouter(model: str, key: str, image: bytes) -> dict:
    import httpx

    data_url = "data:image/png;base64," + base64.b64encode(image).decode()
    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return _parse_json(content)


def _parse_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _to_pixels(payload: dict, width: int, height: int) -> list[dict]:
    labels = []
    for room in payload.get("rooms", []) or []:
        anchor = _anchor(room, width, height)
        if anchor is None:
            continue
        labels.append(
            {
                "kind": "room",
                "name": str(room.get("name", "Room")),
                "type": str(room.get("type", "unknown")),
                "anchor": anchor,
            }
        )
    for exit_ in payload.get("exits", []) or []:
        anchor = _anchor(exit_, width, height)
        if anchor is None:
            continue
        labels.append({"kind": "exit", "name": str(exit_.get("label", "Exit")), "anchor": anchor})
    for stair in payload.get("stairs", []) or []:
        anchor = _anchor(stair, width, height)
        if anchor is None:
            continue
        labels.append({"kind": "stair", "name": str(stair.get("text", "Stair")), "anchor": anchor})
    return labels


def _anchor(item: dict, width: int, height: int) -> tuple[float, float] | None:
    try:
        nx = float(item["nx"])
        nz = float(item["nz"])
    except (KeyError, TypeError, ValueError):
        return None
    return (min(1.0, max(0.0, nx)) * width, min(1.0, max(0.0, nz)) * height)
