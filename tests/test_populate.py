import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import scenario.seed as seed_mod
from scenario.__main__ import main
from scenario.populate import populate
from scenario.schema import validate
from scenario.serve import Handler

ROOT = Path(__file__).resolve().parents[1]
OFFICE = json.loads((ROOT / "fixtures" / "tiny_office.scene.json").read_text())
SCHOOL = json.loads((ROOT / "fixtures" / "two_floor_school.scene.json").read_text())


def _total(groups):
    return sum(g["count"] for g in groups)


@pytest.mark.parametrize("mode", ["load", "random", "llm"])
@pytest.mark.parametrize("total", [1, 7, 120])
def test_places_exactly_total_and_validates(mode, total, monkeypatch):
    monkeypatch.setattr(seed_mod, "complete", lambda prompt, **kw: json.dumps(
        {"rooms": [{"id": "L1_class_n1", "count": 20, "personas": {"older_adult": 1.0}},
                   {"id": "L2_class_s3", "count": 5}]}))
    groups, notes = populate(SCHOOL, total, mode=mode)
    assert _total(groups) == total
    assert validate({"scene": "", "occupants": groups}, SCHOOL) == []


def test_load_mode_follows_design_load_and_skips_circulation():
    groups, notes = populate(OFFICE, 12)
    assert {g["where"]["room"]: g["count"] for g in groups} == {f"L1_office_{r}": 2 for r in
                                                                 ("n1", "n2", "n3", "s1", "s2", "s3")}
    assert not any("exceed" in n for n in notes)


def test_over_design_load_still_places_with_note_and_hard_cap_raises():
    groups, notes = populate(OFFICE, 60)
    assert _total(groups) == 60 and any("exceed the summed IBC design load of 12" in n for n in notes)
    with pytest.raises(ValueError, match="hard cap"):
        populate(OFFICE, 1000)


def test_random_is_seeded_and_reaches_corridors():
    a, _ = populate(OFFICE, 40, mode="random", seed=1)
    b, _ = populate(OFFICE, 40, mode="random", seed=1)
    c, _ = populate(OFFICE, 40, mode="random", seed=2)
    assert a == b and a != c
    assert "L1_corridor" in {g["where"]["room"] for g in a}


def test_dorm_night_puts_two_staff_per_floor_inside_total():
    groups, notes = populate(SCHOOL, 120, preset="dorm_night")
    staff = [g for g in groups if g["personas"] == {"warden": 1.0}]
    assert _total(groups) == 120 and len(staff) == 4
    levels = [g["where"]["room"].split("_")[0] for g in staff]
    assert levels.count("L1") == 2 and levels.count("L2") == 2
    residents = next(g for g in groups if g not in staff)["personas"]
    assert residents["regular_employee"] > 0.9 and 0.01 <= residents["mobility_impaired"] <= 0.02


def test_levels_filter_event_preset_and_custom_mix():
    groups, _ = populate(SCHOOL, 30, preset="event", levels=["L2"])
    assert all(g["where"]["room"].startswith("L2_") for g in groups)
    assert max(groups[0]["personas"], key=groups[0]["personas"].get) == "first_time_visitor"
    custom, _ = populate(OFFICE, 5, preset="custom", personas={"fruit_fly": 1.0})
    assert all(g["personas"] == {"fruit_fly": 1.0} for g in custom)
    with pytest.raises(ValueError, match="unknown levels"):
        populate(SCHOOL, 5, levels=["L9"])
    with pytest.raises(ValueError, match="personas"):
        populate(OFFICE, 5, preset="custom")


def test_cli_writes_and_merges(tmp_path):
    out = tmp_path / "s.json"
    scene = str(ROOT / "fixtures" / "two_floor_school.scene.json")
    assert main(["populate", scene, "--total", "120", "--mode", "load", "--preset", "dorm_night",
                 "--levels", "L1,L2", "-o", str(out)]) == 0
    assert main(["populate", scene, "--total", "10", "--mode", "random", "--into", str(out)]) == 0
    data = json.loads(out.read_text())
    assert _total(data["occupants"]) == 130 and validate(data, SCHOOL) == []


def test_http_populate():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        body = {"scene_path": "fixtures/tiny_office.scene.json", "total": 25, "mode": "random", "seed": 3}
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/populate", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            got = json.load(r)
        assert _total(got["occupants"]) == 25 and got["notes"][0].startswith("populate: 25 people")
        bad = urllib.request.Request(req.full_url, json.dumps({**body, "total": 99999}).encode())
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad, timeout=10)
        assert e.value.code == 400
    finally:
        srv.shutdown()


def test_viewer_request_shape_ai_mode_and_traits(monkeypatch):
    """What blueprint/viewer/src/edit.ts posts: mode "ai", preset custom with personas and slider traits."""
    monkeypatch.setattr(seed_mod, "complete", lambda prompt, **kw: json.dumps({"rooms": []}))
    traits = {"speed_mps": 1.1, "risk_tolerance": 0.2, "ocean": {"O": 0.0, "C": 0.5, "E": 0.0, "A": 0.0, "N": -0.5}}
    groups, _ = populate(OFFICE, 9, mode="ai", preset="custom", personas={"skeptic": 1.0}, traits=traits)
    assert _total(groups) == 9 and all(g["traits"] == traits for g in groups)
    with pytest.raises(ValueError, match="speed_mps"):  # bad sliders are caught by the final schema check
        populate(OFFICE, 9, preset="custom", personas={"skeptic": 1.0}, traits={"speed_mps": 9.0})
