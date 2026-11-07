"""Pydantic models for the frozen scene contract, schema_version 1.0.

Frame and units: meters, right-handed, Y up. The floor plan is the XZ plane:
a 2D point is [x, z], a 3D point is [x, y, z]. Polygon rings carry no repeated
closing point and may have either orientation. Each level's floor sits at
y = elevation_m.

Regenerate the JSON Schema after any (approved) change with:
    python -m contract.models
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_PATH = Path(__file__).with_name("scene.schema.json")

Point2 = Annotated[list[float], Field(min_length=2, max_length=2)]
Point3 = Annotated[list[float], Field(min_length=3, max_length=3)]
Ring = Annotated[list[Point2], Field(min_length=3)]

RoomType = Literal[
    "office", "corridor", "stairwell", "lobby", "restroom", "classroom",
    "storage", "mechanical", "outside", "unknown",
]


class _Model(BaseModel):
    # Unknown keys are rejected; free-form data belongs in Scene.extensions.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Meta(_Model):
    name: str
    source: Literal["blueprint", "lidar", "synthetic"]
    source_file: str
    units: Literal["meters"]
    up_axis: Literal["y"]
    scale_method: Literal["scale_bar", "door_reference", "dimension_text", "manual", "arkit_metric"]
    scale_confidence: float = Field(ge=0.0, le=1.0)
    generated_by: str
    generated_at: str


class Level(_Model):
    id: str
    name: str
    elevation_m: float
    ceiling_height_m: float = Field(gt=0)


class Wall(_Model):
    id: str
    level: str
    polygon: Ring
    height_m: float = Field(gt=0)


class Room(_Model):
    id: str
    level: str
    name: str
    type: RoomType
    polygon: Ring
    area_m2: float = Field(ge=0)
    centroid: Point2
    has_window: bool = False


class Door(_Model):
    id: str
    level: str
    segment: Annotated[list[Point2], Field(min_length=2, max_length=2)]
    width_m: float
    connects: Annotated[list[str], Field(min_length=2, max_length=2)]  # room ids or "outside"
    is_exit: bool
    kind: Literal["swing", "double", "sliding", "opening", "revolving"]
    fire_rated: Optional[bool] = None
    opens_toward: Optional[str] = None  # room id, "outside", or null when unknown


class Exit(_Model):
    id: str
    level: str
    door_id: str
    position: Point2
    width_m: float
    leads_to: Literal["outside", "stairwell", "assembly"]
    label: str
    assembly_point: Optional[Point2] = None


class Stair(_Model):
    id: str
    from_level: str
    to_level: str
    polygon: Ring
    bottom: Point2  # on from_level
    top: Point2  # on to_level
    width_m: float = Field(gt=0)
    direction: Literal["up", "down", "both"]
    is_enclosed: bool


class Elevator(_Model):
    id: str
    levels: Annotated[list[str], Field(min_length=1)]
    position: Point2
    usable_in_fire: bool


class Obstacle(_Model):
    id: str
    level: str
    polygon: Ring
    height_m: float = Field(ge=0)
    kind: Literal["furniture", "equipment", "pillar", "unknown"]
    passable: bool


class PointOfInterest(_Model):
    id: str
    level: str
    position: Point2
    kind: Literal["fire_extinguisher", "alarm_pull", "fire_exit_sign", "assembly_point", "aed", "sprinkler"]


class WalkablePolygon(_Model):
    exterior: Ring
    holes: list[Ring] = []


class WalkableLevel(_Model):
    polygons: list[WalkablePolygon]


class Navmesh(_Model):
    vertices: list[Point3]
    triangles: list[Annotated[list[int], Field(min_length=3, max_length=3)]]
    triangle_level: list[str]
    agent_radius_m: float = Field(gt=0)


class LinkEnd(_Model):
    level: str
    position: Point2


class Link(_Model):
    id: str
    type: Literal["stairs", "door", "ramp"]
    from_: LinkEnd = Field(alias="from")
    to: LinkEnd
    cost_multiplier: float = Field(gt=0)
    bidirectional: bool


class Assets(_Model):
    # Paths relative to the scene.json directory. Required strings since D16.
    display_model: str
    floorplan_overlay: str
    navmesh_debug: str


class SceneWarning(_Model):
    code: str
    message: str
    ref: Optional[str] = None
    severity: Literal["warn", "error"]


class Scene(_Model):
    schema_version: Literal["1.0"]
    meta: Meta
    levels: Annotated[list[Level], Field(min_length=1)]
    walls: list[Wall]
    rooms: list[Room]
    doors: list[Door]
    exits: list[Exit]
    stairs: list[Stair] = []
    elevators: list[Elevator] = []
    obstacles: list[Obstacle] = []
    points_of_interest: list[PointOfInterest] = []
    walkable: dict[str, WalkableLevel]
    navmesh: Navmesh
    links: list[Link] = []
    assets: Assets
    warnings: list[SceneWarning] = []
    extensions: dict[str, Any] = {}


def json_schema() -> dict:
    """Return the JSON Schema (draft 2020-12) for Scene, using JSON field names."""
    schema = Scene.model_json_schema(by_alias=True)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}


if __name__ == "__main__":
    SCHEMA_PATH.write_text(json.dumps(json_schema(), indent=2) + "\n")
    print(f"wrote {SCHEMA_PATH}")
