"""Stimulus grid keys and the response table shared by the builder and decide()."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TABLE_PATH = Path(__file__).parent / "response_table.json"  # not data/: gitignored
# intensity levels per stimulus channel (0 = off is implicit)
LEVELS = {"smoke": (1, 2), "loom": (1,), "light": (1,), "sound": (1,)}


def key(cell: dict) -> str:
    """Canonical table key, e.g. 'smoke=L2|loom=-0|light=R1|sound=B1'."""
    parts = []
    for ch in LEVELS:
        side, level = cell.get(ch, ("-", 0))
        parts.append(f"{ch}={side if level else '-'}{level}")
    return "|".join(parts)


@lru_cache(maxsize=1)
def table() -> dict:
    return json.loads(TABLE_PATH.read_text())
