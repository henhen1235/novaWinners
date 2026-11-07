"""Vision floor descriptions become a validated scene. No network."""

import json
from pathlib import Path

import cv2
import numpy as np

from contract.validate_scene import validate_data
from ingest_blueprint.llm_floor import (
    choose_building,
    classify_sheet,
    expand_floor_labels,
    group_buildings,
    parse_model_json,
    px_per_m_from_text,
    read_building,
    scene_from_description,
)
from nav.pathfind import path_to_exit

OFFICE = {
    "building": "Office",
    "floor": "1F",
    "scale": "1/200",
    "rooms": [
        {"name": "Office", "type": "office", "x": 0.15, "z": 0.38, "w": 0.25, "h": 0.30},
        {"name": "Corridor", "type": "corridor", "x": 0.15, "z": 0.20, "w": 0.55, "h": 0.18},
        {"name": "Stair", "type": "stairwell", "x": 0.45, "z": 0.38, "w": 0.25, "h": 0.30},
    ],
    "doors": [
        {"x": 0.27, "z": 0.38},
        {"x": 0.57, "z": 0.38},
    ],
    "exits": [{"x": 0.15, "z": 0.29, "label": "Exit"}],
    "stairs": [{"x": 0.55, "z": 0.50, "name": "Stair"}],
}


def test_printed_scale_and_markdown_json():
    assert abs(px_per_m_from_text("1/200") - 39.37) < 0.01
    assert abs(px_per_m_from_text("1/500") - 15.75) < 0.01
    parsed = parse_model_json('```json\n{"scale": "1/200", "rooms": []}\n```')
    assert parsed["scale"] == "1/200"


def test_city_hotel_sheets_are_chosen_and_typical_floors_repeat():
    titles = [
        (0, "モデル建物（事務所、5,000㎡） 1階平面図 1/200"),
        (1, "モデル建物（事務所、5,000㎡） 2～6階平面図 1/200"),
        (2, "モデル建物（事務所、5,000㎡） A－A'断面図 1/200"),
        (3, "モデル建物（ビジネスホテル、5,000㎡） 1階平面図 1/500"),
        (6, "モデル建物（ビジネスホテル、5,000㎡） 階高及び天井高 1/500"),
        (7, "モデル建物（シティホテル、16,700㎡） B1階平面図 1/500"),
        (8, "モデル建物（シティホテル、16,700㎡） 1階平面図 1/500"),
        (9, "モデル建物（シティホテル、16,700㎡） 2階平面図 1/500"),
        (10, "モデル建物（シティホテル、16,700㎡） 3階平面図 1/500"),
        (11, "モデル建物（シティホテル、16,700㎡） 4～12階平面図 1/500"),
        (12, "モデル建物（シティホテル、16,700㎡） 13階平面図 1/500"),
        (13, "モデル建物（シティホテル、16,700㎡） R階平面図 1/500"),
        (14, "モデル建物（シティホテル、16,700㎡） 客室基準平面図 1/100"),
        (15, "モデル建物（シティホテル、16,700㎡） A－A'断面図 1/500"),
        (48, "モデル建物（大学） 配置図 1/500"),
        (49, "モデル建物（大学＿高層棟、7,950㎡） 1階平面図 1/200"),
        (50, "モデル建物（大学＿高層棟、7,950㎡） 2階平面図 1/200"),
        (51, "モデル建物（大学＿高層棟、7,950㎡） 3階平面図 1/200"),
        (52, "モデル建物（大学＿高層棟、7,950㎡） 4～5階平面図 1/200"),
        (53, "モデル建物（大学＿高層棟、7,950㎡） 6～7階平面図 1/200"),
        (54, "モデル建物（大学＿高層棟、7,950㎡） R階平面図 1/200"),
    ]
    chosen = choose_building([classify_sheet(index, text) for index, text in titles])
    assert [sheet.page_index for sheet in chosen] == [7, 8, 9, 10, 11, 12, 13]
    assert "シティホテル" in chosen[0].building
    floors = []
    for sheet in chosen:
        floors.extend(expand_floor_labels(sheet.floor_label))
    floors.sort()
    names = [name for _key, name in floors]
    assert names == ["B1", "1F", "2F", "3F", "4F", "5F", "6F", "7F", "8F", "9F", "10F", "11F", "12F", "13F", "RF"]
    assert len(expand_floor_labels("4～12階平面図")) == 9
    grouped = group_buildings([classify_sheet(index, text) for index, text in titles])
    assert [item["name"] for item in grouped] == ["事務所", "ビジネスホテル", "シティホテル", "大学_高層棟"]
    city = grouped[2]
    assert city["slug"] == "city-hotel"
    assert city["gloss"] == "City hotel"
    assert [sheet["page"] for sheet in city["sheets"]] == [8, 9, 10, 11, 12, 13, 14]
    assert city["floors"] == ["B1", "1F", "2F", "3F", "4F", "5F", "6F", "7F", "8F", "9F", "10F", "11F", "12F", "13F", "RF"]
    assert grouped[3]["gloss"] == "University high-rise"
    titles_text = " ".join(sheet["title"] for item in grouped for sheet in item["sheets"])
    assert "断面" not in titles_text
    assert "配置" not in titles_text
    assert "階高" not in titles_text
    assert "詳細" not in titles_text


def test_rectangle_floor_validates_and_typical_floors_share_geometry():
    scene = scene_from_description(OFFICE, width_px=1000, height_px=800, floor_label="1～2階平面図")
    errors = [issue for issue in validate_data(scene) if issue["severity"] == "error"]
    assert errors == []
    assert len(scene["rooms"]) >= 1
    assert len(scene["doors"]) >= 1
    assert scene["navmesh"]["triangles"]
    assert len(scene["levels"]) == 2
    assert scene["levels"][1]["elevation_m"] == 3.0
    assert scene["links"]
    lower = [room for room in scene["rooms"] if room["level"] == "L1"]
    upper = [room for room in scene["rooms"] if room["level"] == "L2"]
    assert len(lower) == len(upper) >= 3
    start = next(room["centroid"] for room in lower if room["type"] == "office")
    path = path_to_exit(scene["navmesh"], (start[0], start[1]), scene["exits"], level="L1")
    assert path["distance_m"] is not None
    assert path["distance_m"] > 0


def test_read_building_caches_the_model_response(tmp_path: Path, monkeypatch):
    image = np.full((800, 1000, 3), 255, np.uint8)
    cv2.imwrite(str(tmp_path / "plan.png"), image)
    calls = {"n": 0}

    def fake_call(model, key, prompt, image_bytes):
        calls["n"] += 1
        assert key == "test-key"
        assert image_bytes
        return OFFICE

    monkeypatch.setattr("ingest_blueprint.llm_floor._call_openrouter", fake_call)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    first = read_building(tmp_path / "plan.png", tmp_path / "out")
    second = read_building(tmp_path / "plan.png", tmp_path / "out")
    assert calls["n"] == 1
    assert first is not None and second is not None
    assert first["building"] == "Office"
    assert first["floors"] == 1
    assert first["reader"] == "llm"
    assert (tmp_path / "out" / "scene.json").is_file()
    assert (tmp_path / "out" / "display.glb").is_file()
    stored = json.loads(next((tmp_path / "out" / "llm_cache").glob("*.json")).read_text(encoding="utf-8"))
    assert stored["rooms"][0]["name"] == "Office"
    errors = [issue for issue in validate_data(first["scene"]) if issue["severity"] == "error"]
    assert errors == []
