import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools.render_plan import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("fixture", ["tiny_office", "two_floor_school"])
def test_plan_svg_names_every_room_and_exit(fixture, tmp_path):
    scene_path = ROOT / "fixtures" / f"{fixture}.scene.json"
    scene = json.loads(scene_path.read_text())
    out = tmp_path / "plan.svg"

    assert main([str(scene_path), "-o", str(out)]) == 0

    root = ET.parse(out).getroot()  # raises if the SVG is not well-formed XML
    text = " ".join("".join(el.itertext()) for el in root.iter())
    room_ids = {el.get("data-id") for el in root.iter() if el.get("class") == "room"}
    assert room_ids == {room["id"] for room in scene["rooms"]}
    for room in scene["rooms"]:
        assert room["name"] in text
    for exit_ in scene["exits"]:
        assert exit_["label"] in text


def test_tokens_css_matches_tokens_json():
    tokens = json.loads((ROOT / "design" / "tokens.json").read_text())
    css = (ROOT / "design" / "tokens.css").read_text()
    for group, values in tokens.items():
        for name, value in values.items():
            assert f"--{group}-{name}: {value};" in css
