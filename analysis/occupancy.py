"""Occupant load per room: area / IBC Table 1004.5 load factor, rounded up per room.

Factors are ft^2 per occupant from IBC 2021 Table 1004.5, quoted from memory and
NOT re-checked against the adopted code text today (verified=False on every row);
read the table before any of these numbers goes on a slide.
Circulation and accessory spaces get 0: their occupants are counted in the rooms
they serve (our heuristic; the code applies gross factors to the whole floor).
"""

from __future__ import annotations

import math

FT2_PER_M2 = 10.7639

# room type -> (IBC function of space, ft^2 per occupant, gross|net, verified)
FACTORS: dict[str, tuple[str, float, str, bool]] = {
    "office": ("Business areas", 150, "gross", False),
    "classroom": ("Educational, classroom area", 20, "net", False),
    "lobby": ("Assembly without fixed seats, unconcentrated (tables and chairs)", 15, "net", False),
    "storage": ("Accessory storage areas, mechanical equipment room", 300, "gross", False),
    "mechanical": ("Accessory storage areas, mechanical equipment room", 300, "gross", False),
    "unknown": ("Business areas (default for unlabeled rooms)", 150, "gross", False),
}
ZERO = {"corridor", "restroom", "stairwell", "outside"}  # counted in the rooms they serve [H]


def room_load(room: dict) -> dict:
    """Occupant load for one scene room."""
    t = room["type"]
    if t in ZERO:
        return {"load": 0, "basis": f"{t}: circulation/accessory, occupants counted in served rooms [H]",
                "factor_ft2": None, "verified": False}
    use, ft2, kind, verified = FACTORS.get(t, FACTORS["unknown"])
    load = math.ceil(room["area_m2"] * FT2_PER_M2 / ft2 - 1e-9)
    return {"load": load, "basis": f"IBC 2021 Table 1004.5: {use}, {ft2:g} ft2 {kind}",
            "factor_ft2": ft2, "verified": verified}


def analyze(scene: dict) -> dict:
    rooms = {r["id"]: room_load(r) for r in scene["rooms"]}
    return {"rooms": rooms, "total": sum(r["load"] for r in rooms.values()),
            "note": "IBC factors from memory, unverified; fractions rounded up per room"}
