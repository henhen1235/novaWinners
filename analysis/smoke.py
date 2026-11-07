"""Smoke clock: time for the smoke layer to fall to head height if a fire starts in the room.

An ESTIMATE from a closed-form zone equation, not a fire model.
Tanaka and Yamana 1985 (Fire Science and Technology 5(1):31-40, Eqs. 1.6-1.9,
checked in research/MAPPING_IDEAS.md idea 3): closed room, plan area A, ceiling H,
fire Q = alpha t^n with n = 2 (t-squared):

    k    = C / rho_s, C = 0.076 kg/(s kW^(1/3) m^(5/3)) (Zukoski plume constant)
    t(Z) = (n+3)/2 * [ (Z^(-2/3) - H^(-2/3)) * A / (k alpha^(1/3)) ]^(3/(n+3))

Assumptions: room of origin only; no venting, leakage or door flow to
neighbours; uniform smoke layer density rho_s = 1.1 kg/m3 (early-stage
fire); axisymmetric plume from floor level; uniform ceiling height (the
level's ceiling_height_m); fast t^2 growth alpha = 0.0469 kW/s^2 (standard
design value, not re-opened today). Check: 18.7 m2, H 3.0 m gives 49.8 s
(research table: 49.6 s with C = 0.0764).
"""

from __future__ import annotations

ZUKOSKI_C = 0.076
RHO_SMOKE = 1.1  # kg/m3
ALPHA_FAST = 0.0469  # kW/s^2
HEAD_HEIGHT_M = 1.8
N = 2


def fill_time_s(area_m2: float, ceiling_m: float, alpha: float = ALPHA_FAST, z_m: float = HEAD_HEIGHT_M) -> float:
    """Seconds until the layer interface reaches z_m; 0 when the ceiling is at or below z_m."""
    if ceiling_m <= z_m:
        return 0.0
    k = ZUKOSKI_C / RHO_SMOKE
    inner = (z_m ** (-2 / 3) - ceiling_m ** (-2 / 3)) * area_m2 / (k * alpha ** (1 / 3))
    return (N + 3) / 2 * inner ** (3 / (N + 3))


def analyze(scene: dict) -> dict:
    ceiling = {lv["id"]: lv["ceiling_height_m"] for lv in scene["levels"]}
    rooms = {r["id"]: {"ceiling_m": ceiling[r["level"]], "area_m2": r["area_m2"],
                       "t_head_height_s": round(fill_time_s(r["area_m2"], ceiling[r["level"]]), 1)}
             for r in scene["rooms"] if r["type"] != "outside"}
    return {"rooms": rooms, "fire": "t-squared fast, alpha 0.0469 kW/s^2", "head_height_m": HEAD_HEIGHT_M,
            "label": "estimate (Tanaka-Yamana closed-room zone equation), not a fire model",
            "assumptions": ["room of fire origin only", "no venting, leakage or flow through doors",
                            "uniform smoke density 1.1 kg/m3", "level ceiling height used for every room",
                            "Zukoski plume constant 0.076"]}
