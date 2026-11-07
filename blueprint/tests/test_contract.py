"""Contract schema and validator fixtures."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from contract.models import SCHEMA_PATH, Scene, json_schema
from contract.validate_scene import validate

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent  # the contract package lives at the repo root
PYTHON = sys.executable


def test_schema_file_matches_models() -> None:
    assert json_schema() == json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_path(path: Path) -> list:
    return validate(json.loads(path.read_text(encoding="utf-8")))


def test_tiny_office_shape() -> None:
    data = json.loads((ROOT / "fixtures" / "tiny_office.scene.json").read_text(encoding="utf-8"))
    scene = Scene.model_validate(data)
    assert len(scene.rooms) == 7
    assert sum(1 for room in scene.rooms if room.type == "corridor") == 1
    assert len(scene.exits) == 2
    assert scene.levels[0].elevation_m == 0.0
    assert validate_path(ROOT / "fixtures" / "tiny_office.scene.json") == []


def test_two_floor_school_shape() -> None:
    path = ROOT / "fixtures" / "two_floor_school.scene.json"
    scene = Scene.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert {level.id for level in scene.levels} == {"L1", "L2"}
    assert len(scene.stairs) == 2
    assert {link.cost_multiplier for link in scene.links} == {2.0}
    assert validate_path(path) == []


def test_validator_cli_accepts_tiny_office() -> None:
    path = ROOT / "fixtures" / "tiny_office.scene.json"
    result = subprocess.run(
        [PYTHON, "-m", "contract.validate_scene", str(path), "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_negative_fixtures() -> None:
    expected = {
        "no_exits": ("NO_EXITS", 1),
        "room_unreachable": ("ROOM_UNREACHABLE", 1),
        "exit_unreachable": ("EXIT_UNREACHABLE", 1),
        "door_too_narrow": ("DOOR_TOO_NARROW", 1),
        "small_room_artifact": ("SMALL_ROOM_ARTIFACT", 0),
        "scale_low_confidence": ("SCALE_LOW_CONFIDENCE", 0),
        "unknown_space_near_door": ("UNKNOWN_SPACE_NEAR_DOOR", 0),
        "leaky_wall_suspected": ("LEAKY_WALL_SUSPECTED", 0),
        "orphan_stair": ("ORPHAN_STAIR", 0),
    }
    for name, (code, exit_code) in expected.items():
        path = ROOT / "fixtures" / "negative" / f"{name}.scene.json"
        result = subprocess.run(
            [PYTHON, "-m", "contract.validate_scene", str(path), "--json"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == exit_code, f"{name}: {result.stdout}"
        issues = json.loads(result.stdout)["issues"]
        assert any(issue["code"] == code for issue in issues), name
