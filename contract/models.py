"""Pydantic models for scene.json schema 1.0.

Coordinates are meters. The floor plan is the XZ plane and Y is up.
A 2D point is [x, z]. A 3D point is [x, y, z].
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


Point2D = list[float]
Point3D = list[float]

RoomType = Literal[
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
]
DoorKind = Literal["swing", "double", "sliding", "opening", "revolving"]
ObstacleKind = Literal["furniture", "equipment", "pillar", "unknown"]
PoiKind = Literal[
    "fire_extinguisher",
    "alarm_pull",
    "fire_exit_sign",
    "assembly_point",
    "aed",
    "sprinkler",
]
ExitLeadsTo = Literal["outside", "stairwell", "assembly"]
StairDirection = Literal["up", "down", "both"]
LinkType = Literal["stairs", "door", "ramp"]
ScaleMethod = Literal[
    "scale_bar",
    "door_reference",
    "dimension_text",
    "manual",
    "arkit_metric",
]
SourceKind = Literal["blueprint", "lidar", "synthetic"]
Severity = Literal["warn", "error"]


class Meta(_Model):
    name: str
    source: SourceKind
    source_file: str
    units: Literal["meters"] = "meters"
    up_axis: Literal["y"] = "y"
    scale_method: ScaleMethod
    scale_confidence: float = Field(ge=0.0, le=1.0)
    generated_by: str
    generated_at: str


class Level(_Model):
    id: str
    name: str
    elevation_m: float
    ceiling_height_m: float


class Wall(_Model):
    id: str
    level: str
    polygon: list[Point2D]
    height_m: float


class Room(_Model):
    id: str
    level: str
    name: str
    type: RoomType
    polygon: list[Point2D]
    area_m2: float
    centroid: Point2D
    has_window: bool = False


class Door(_Model):
    id: str
    level: str
    segment: list[Point2D]
    width_m: float
    connects: list[str]
    is_exit: bool = False
    kind: DoorKind = "swing"
    fire_rated: bool | None = None
    opens_toward: str | None = None


class Exit(_Model):
    id: str
    level: str
    door_id: str
    position: Point2D
    width_m: float
    leads_to: ExitLeadsTo
    label: str
    assembly_point: Point2D | None = None


class Stair(_Model):
    id: str
    from_level: str
    to_level: str
    polygon: list[Point2D]
    bottom: Point2D
    top: Point2D
    width_m: float
    direction: StairDirection = "both"
    is_enclosed: bool = True


class Elevator(_Model):
    id: str
    levels: list[str]
    position: Point2D
    usable_in_fire: bool = False


class Obstacle(_Model):
    id: str
    level: str
    polygon: list[Point2D]
    height_m: float
    kind: ObstacleKind = "unknown"
    passable: bool = False


class PointOfInterest(_Model):
    id: str
    level: str
    position: Point2D
    kind: PoiKind


class WalkablePolygon(_Model):
    exterior: list[Point2D]
    holes: list[list[Point2D]] = Field(default_factory=list)


class LevelWalkable(_Model):
    polygons: list[WalkablePolygon]


class Navmesh(_Model):
    vertices: list[Point3D]
    triangles: list[list[int]]
    triangle_level: list[str]
    agent_radius_m: float


class LinkEnd(_Model):
    level: str
    position: Point2D


class Link(_Model):
    id: str
    type: LinkType
    from_: LinkEnd = Field(alias="from")
    to: LinkEnd
    cost_multiplier: float = 1.0
    bidirectional: bool = True

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Assets(_Model):
    display_model: str
    floorplan_overlay: str
    navmesh_debug: str


class SceneWarning(_Model):
    code: str
    message: str
    ref: str | None = None
    severity: Severity


class Scene(_Model):
    """Top-level scene contract, schema version 1.0."""

    schema_version: Literal["1.0"] = "1.0"
    meta: Meta
    levels: list[Level]
    walls: list[Wall]
    rooms: list[Room]
    doors: list[Door]
    exits: list[Exit]
    stairs: list[Stair] = Field(default_factory=list)
    elevators: list[Elevator] = Field(default_factory=list)
    obstacles: list[Obstacle] = Field(default_factory=list)
    points_of_interest: list[PointOfInterest] = Field(default_factory=list)
    walkable: dict[str, LevelWalkable]
    navmesh: Navmesh
    links: list[Link] = Field(default_factory=list)
    assets: Assets
    warnings: list[SceneWarning] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)
