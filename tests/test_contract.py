"""Contract schema, fixtures and validator codes (DECISIONS.md D3 to D5)."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from contract.models import SCHEMA_PATH, json_schema
from contract.validate_scene import validate
from tools.layouts import build_scene, corridor_office, tiny_office, two_floor_school

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
NEGATIVES = sorted((FIXTURES / "negative").glob("*.scene.json"))
SPECS = {"tiny_office": tiny_office, "two_floor_school": two_floor_school}


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _errors(scene: dict) -> list[dict]:
    return [i for i in validate(scene) if i["severity"] == "error"]


def test_committed_schema_matches_models() -> None:
    assert _load(SCHEMA_PATH) == json_schema()


@pytest.mark.parametrize("name", sorted(SPECS))
def test_fixture_is_clean(name: str) -> None:
    assert validate(_load(FIXTURES / f"{name}.scene.json")) == []


@pytest.mark.parametrize("name", sorted(SPECS))
def test_fixture_matches_builder(name: str) -> None:
    """Committed fixtures are what `python -m tools.layouts` produces (git sha aside)."""
    committed, built = _load(FIXTURES / f"{name}.scene.json"), build_scene(SPECS[name]())
    committed["meta"].pop("generated_by")
    built["meta"].pop("generated_by")
    assert committed == built


def test_school_upper_floor_reachable_only_by_stairs() -> None:
    scene = _load(FIXTURES / "two_floor_school.scene.json")
    scene["stairs"], scene["links"] = [], []
    unreachable = {i["ref"] for i in validate(scene) if i["code"] == "ROOM_UNREACHABLE"}
    upper = {r["id"] for r in scene["rooms"] if r["level"] == "L2"}
    assert unreachable == upper


WARN_CODES = {
    "SMALL_ROOM_ARTIFACT", "ORPHAN_STAIR", "SCALE_LOW_CONFIDENCE",
    "LEAKY_WALL_SUSPECTED", "UNKNOWN_SPACE_NEAR_DOOR",
}


def test_every_code_has_a_negative_fixture() -> None:
    """Error-code fixtures come from tools.layouts; the warn-code and EXIT_UNREACHABLE ones from blueprint/ (D17)."""
    assert {p.name.split(".")[0] for p in NEGATIVES} == WARN_CODES | {
        "SCHEMA_INVALID", "DUPLICATE_ID", "BAD_REFERENCE", "INVALID_POLYGON", "EXTENT_OUT_OF_RANGE",
        "DOOR_TOO_NARROW", "NO_EXITS", "ROOM_UNREACHABLE", "NAVMESH_INVALID", "NAVMESH_ISLAND",
        "EXIT_UNREACHABLE",
    }


@pytest.mark.parametrize("path", NEGATIVES, ids=lambda p: p.name.split(".")[0])
def test_negative_fixture_yields_its_code(path: Path) -> None:
    code = path.name.split(".")[0]
    issues = validate(_load(path))
    assert code in {i["code"] for i in issues}
    # Warn-code fixtures carry only warnings; every other fixture must fail validation.
    assert any(i["severity"] == "error" for i in issues) == (code not in WARN_CODES)


def test_cli_exit_codes() -> None:
    def run(path: Path) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "contract.validate_scene", str(path), "--json"]
        return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=60)

    good, bad = run(FIXTURES / "tiny_office.scene.json"), run(FIXTURES / "negative" / "NO_EXITS.scene.json")
    assert good.returncode == 0, good.stdout + good.stderr
    assert bad.returncode != 0
    assert "NO_EXITS" in {i["code"] for i in json.loads(bad.stdout)["issues"]}


@pytest.mark.parametrize("seed", range(8))
def test_corridor_office_is_clean(seed: int) -> None:
    scene = build_scene(corridor_office(seed))
    assert 5 <= len(scene["rooms"]) <= 11  # 4 to 10 rooms plus the corridor
    assert validate(scene) == []


def test_validator_survives_broken_references() -> None:
    """Bad references everywhere produce issues, never an exception or CHECK_FAILED."""
    scene = _load(FIXTURES / "two_floor_school.scene.json")
    scene["doors"][0]["connects"] = ["nope", "outside"]
    scene["doors"][1]["opens_toward"] = "zzz"
    scene["stairs"][0]["from_level"] = "L9"
    scene["links"][0]["to"]["level"] = "L7"
    scene["exits"][0]["door_id"] = "missing"
    scene["elevators"][0]["levels"] = ["LX"]
    scene["navmesh"]["triangle_level"][0] = "L8"
    scene["rooms"][0]["polygon"] = [[0, 0], [0, 0], [0, 0]]
    codes = {i["code"] for i in validate(scene)}
    assert {"BAD_REFERENCE", "INVALID_POLYGON", "NAVMESH_INVALID", "ORPHAN_STAIR"} <= codes
    assert "CHECK_FAILED" not in codes


@pytest.mark.parametrize("junk", [{}, [], None, "scene", {"schema_version": "1.0"}])
def test_validator_rejects_non_scenes(junk: object) -> None:
    assert {i["code"] for i in validate(junk)} == {"SCHEMA_INVALID"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda n: n.update(triangles=[[10**30, 0, 1]], triangle_level=["L1"]),
        lambda n: n["vertices"].__setitem__(0, [float("nan")] * 3),
        lambda n: n["triangle_level"].pop(),
        lambda n: (n["triangles"].append([0, 0, 0]), n["triangle_level"].append("L1")),
    ],
    ids=["huge-index", "nan-vertex", "level-count", "degenerate"],
)
def test_navmesh_corruption_is_invalid(mutate) -> None:
    scene = copy.deepcopy(_load(FIXTURES / "tiny_office.scene.json"))
    mutate(scene["navmesh"])
    assert "NAVMESH_INVALID" in {i["code"] for i in _errors(scene)}
