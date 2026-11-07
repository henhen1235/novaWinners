import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from analysis import occupancy, pinch, smoke, spof
from analysis.__main__ import main

ROOT = Path(__file__).resolve().parents[1]


def _scene(name="tiny_office"):
    return json.loads((ROOT / "fixtures" / f"{name}.scene.json").read_text())


def test_corridor_is_the_single_point_of_failure():
    result = spof.analyze(_scene())
    assert result["articulation_rooms"] == ["L1_corridor"]
    top = result["ranked"][0]
    assert (top["kind"], top["id"]) == ("room", "L1_corridor")
    assert len(top["stranded_rooms"]) == 6
    # Two exits join corridor and outside: a MultiGraph must not call either a bridge.
    assert "door_07" not in result["bridge_doors"] and "door_08" not in result["bridge_doors"]


def test_losing_main_entrance_leaves_47_percent_exit_width():
    rows = {r["id"]: r for r in spof.analyze(_scene())["ranked"]}
    assert rows["door_08"]["exit_width_left_share"] == pytest.approx(0.471, abs=1e-3)
    assert rows["door_08"]["stranded_rooms"] == []
    assert rows["door_01"]["leaf"] is True


def test_stairs_join_levels_in_spof_graph():
    result = spof.analyze(_scene("two_floor_school"))
    assert result["unreachable_rooms"] == []
    assert set(result["articulation_rooms"]) >= {"L1_corridor", "L2_corridor"}


def test_occupant_load_matches_ibc_office_factor():
    occ = occupancy.analyze(_scene())
    assert occ["total"] == 12  # 4 x ceil(18.7 m2 / 150 ft2) + 2 x ceil(22.5 m2 / 150 ft2)
    assert occ["rooms"]["L1_corridor"]["load"] == 0


def test_smoke_clock_matches_research_table():
    assert smoke.fill_time_s(18.7, 3.0) == pytest.approx(49.6, abs=0.5)
    assert smoke.fill_time_s(20, 2.4) < smoke.fill_time_s(20, 4.0)
    assert smoke.fill_time_s(20, 1.7) == 0.0


def test_office_route_pinch_is_its_09_m_door():
    rows = {r["room"]: r for r in pinch.analyze(_scene())["rooms"]}
    n1 = rows["L1_office_n1"]
    assert n1["pinch_width_m"] == pytest.approx(0.9, abs=0.06)
    assert n1["pinch_at"][0] == pytest.approx(2.5, abs=0.15) and n1["pinch_at"][1] == pytest.approx(4.0, abs=0.15)
    assert n1["flow_p_per_min"] == pytest.approx(70, abs=5)
    assert rows["L1_corridor"]["pinch_width_m"] == pytest.approx(1.6)  # exit door width stands in near the exit


def test_cli_writes_json_and_overlay(tmp_path):
    out = tmp_path / "analysis.json"
    assert main([str(ROOT / "fixtures" / "tiny_office.scene.json"), "-o", str(out)]) == 0
    data = json.loads(out.read_text())
    assert set(data) >= {"occupancy", "spof", "pinch", "smoke"}
    svg = ET.parse(tmp_path / "plan_analysis.svg").getroot()
    text = " ".join("".join(el.itertext()) for el in svg.iter())
    assert "SINGLE POINT OF FAILURE" in text and "smoke at 1.8 m" in text
