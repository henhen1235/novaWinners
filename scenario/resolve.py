"""Resolve a user's words ("the kitchen", "east exit", "floor 2") to scene ids, no LLM.

Order of strength: exact id, exact name/label, room-type word, compass or
"main" for exits, then fuzzy name similarity. Compass convention until scenes
carry a bearing: +x = east, -z = north (top-down viewer); "main" exit = widest.
Callers treat zero candidates, or a top tie, as ambiguous and must ask the user.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

KINDS = ("room", "exit", "door", "level")
MIN_SCORE = 0.5
TIE = 0.05
STOP = {"the", "a", "an", "in", "at", "of", "on", "by", "near", "room"}
TYPE_WORDS = {  # word in the user's text -> scene room type
    "corridor": "corridor", "hallway": "corridor", "hall": "corridor", "passage": "corridor",
    "stairwell": "stairwell", "stairs": "stairwell", "staircase": "stairwell", "stair": "stairwell",
    "lobby": "lobby", "reception": "lobby", "foyer": "lobby", "entrance": "lobby",
    "restroom": "restroom", "bathroom": "restroom", "toilet": "restroom", "washroom": "restroom",
    "classroom": "classroom", "class": "classroom", "office": "office", "storage": "storage",
    "closet": "storage", "mechanical": "mechanical", "plant": "mechanical",
}
COMPASS = {"east": (0, 1), "west": (0, -1), "south": (1, 1), "north": (1, -1)}  # (axis, sign)
EXIT_WORDS = {"exit", "door", "entrance", "way", "out", "doorway", "fire"}


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9_]+", text.lower()))


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t not in STOP}


def _pool(scene: dict) -> list[dict]:
    rooms = {r["id"]: r["name"] for r in scene["rooms"]}
    out = [{"kind": "room", "id": r["id"], "label": r["name"], "level": r["level"], "type": r["type"]}
           for r in scene["rooms"] if r["type"] != "outside"]
    out += [{"kind": "exit", "id": e["id"], "label": e["label"], "level": e["level"], "position": e["position"],
             "width_m": e["width_m"]} for e in scene["exits"]]
    out += [{"kind": "door", "id": d["id"], "level": d["level"],
             "label": "door between " + " and ".join(rooms.get(c, c) for c in d["connects"])} for d in scene["doors"]]
    levels = sorted(scene["levels"], key=lambda lv: lv["elevation_m"])
    out += [{"kind": "level", "id": lv["id"], "label": lv["name"], "level": lv["id"], "floor": i + 1}
            for i, lv in enumerate(levels)]
    return out


def _score(c: dict, q: str, qt: set[str], exits: list[dict]) -> tuple[float, str]:
    if q == c["id"].lower():
        return 1.0, "exact id"
    label = _norm(c["label"])
    if q == label:
        return 0.95, "exact name"
    best = (0.0, "")
    if c["kind"] == "room" and {TYPE_WORDS[t] for t in qt if t in TYPE_WORDS} == {c["type"]}:
        best = (0.8, f"room type {c['type']}")
    if c["kind"] == "exit" and exits:
        for word, (axis, sign) in COMPASS.items():
            if word in qt and c is max(exits, key=lambda e: sign * e["position"][axis]):
                best = max(best, (0.8, f"{word}most exit (+x east, -z north convention)"))
        if "main" in qt and c["width_m"] == max(e["width_m"] for e in exits):
            best = max(best, (0.75, "widest exit taken as main"))
        if not qt - EXIT_WORDS:
            best = max(best, (0.6, "any exit"))
    if c["kind"] == "level":
        nums = [int(t) for t in qt if t.isdigit()]
        if nums == [c["floor"]] and qt & {"floor", "level", "storey", "story"}:
            best = max(best, (0.9, f"floor {c['floor']} counted from the lowest level"))
        if c["floor"] == 1 and qt & {"ground", "first"}:
            best = max(best, (0.85, "ground = lowest level"))
    fuzzy = SequenceMatcher(None, " ".join(sorted(qt)), " ".join(sorted(_tokens(c["label"])))).ratio()
    overlap = len(qt & _tokens(c["label"])) / max(1, len(qt | _tokens(c["label"])))
    return max(best, (round(0.7 * max(fuzzy, overlap), 3), "similar name"))


def resolve_location(scene: dict, text: str, kind: str = "any", level: str | None = None) -> dict:
    """Candidates for `text`, best first, with an `ambiguous` flag (none, or a top tie)."""
    q, qt = _norm(text), _tokens(text)
    pool = [c for c in _pool(scene) if kind in ("any", c["kind"]) and (level is None or c["level"] == level)]
    exits = [c for c in pool if c["kind"] == "exit"]
    scored = []
    for c in pool:
        s, why = _score(c, q, qt, exits)
        if s >= MIN_SCORE:
            scored.append({"kind": c["kind"], "id": c["id"], "label": c["label"], "level": c["level"],
                           "score": s, "why": why})
    scored.sort(key=lambda c: -c["score"])
    ambiguous = not scored or (len(scored) > 1 and scored[1]["score"] >= scored[0]["score"] - TIE)
    return {"query": text, "candidates": scored[:6], "ambiguous": ambiguous}
