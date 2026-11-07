from pathlib import Path

from fastapi.testclient import TestClient

from editor.app import create_app
from ingest_blueprint.pipeline import run
from tools.make_synthetic_plan import write_plan


def test_upload_generates_a_model(monkeypatch):
    monkeypatch.setattr("editor.app.read_building", lambda *_args, **_kwargs: None)
    pdf = Path("samples/blueprints/office_vector/plan.pdf")
    client = TestClient(create_app())
    response = client.post("/api/generate", files={"file": ("office.pdf", pdf.read_bytes(), "application/pdf")})
    assert response.status_code == 200
    body = response.json()
    assert body["exit_code"] == 0
    assert body["rooms"] >= 5
    assert body["reader"] == "vector"
    assert body["floors"] >= 1
    model = client.get(body["glb_url"])
    scene = client.get(body["scene_url"])
    assert model.status_code == 200
    assert model.content[:4] == b"glTF"
    assert scene.status_code == 200
    assert scene.json()["rooms"]
    rejected = client.post("/api/generate", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert rejected.status_code == 400


def test_upload_reports_the_vision_building(monkeypatch):
    pdf = Path("samples/blueprints/office_vector/plan.pdf")

    def fake(source, out_dir):
        scene, code = run(source, out_dir, no_llm=True)
        return {"scene": scene, "exit_code": code, "building": "シティホテル", "floors": 15, "reader": "llm"}

    monkeypatch.setattr("editor.app.read_building", fake)
    client = TestClient(create_app())
    response = client.post("/api/generate", files={"file": ("hotel.pdf", pdf.read_bytes(), "application/pdf")})
    assert response.status_code == 200
    body = response.json()
    assert body["building"] == "シティホテル"
    assert body["floors"] == 15
    assert body["reader"] == "llm"
    assert body["rooms"] >= 5
    assert client.get(body["glb_url"]).content[:4] == b"glTF"


def test_buildings_route_lists_titles_without_calling_the_model(monkeypatch, tmp_path: Path):
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    called = {"model": 0}

    def fake_read(*_args, **_kwargs):
        called["model"] += 1
        return None

    monkeypatch.setattr("editor.app.read_building", fake_read)
    monkeypatch.setattr(
        "editor.app.list_buildings",
        lambda _path: [
            {
                "name": "事務所",
                "slug": "office",
                "gloss": "Office",
                "key": "事務所",
                "sheets": [{"page": 1, "title": "1階平面図", "floors": ["1F"]}],
                "floors": ["1F"],
            },
            {
                "name": "シティホテル",
                "slug": "city-hotel",
                "gloss": "City hotel",
                "key": "シティホテル",
                "sheets": [{"page": 8, "title": "B1階平面図", "floors": ["B1"]}],
                "floors": ["B1", "1F"],
            },
        ],
    )
    monkeypatch.setattr(
        "editor.app._scene_index",
        lambda: {
            "シティホテル": {
                "run_id": "c17e107e1501",
                "floors": 4,
                "scene_url": "/api/runs/c17e107e1501/scene.json",
                "glb_url": "/api/runs/c17e107e1501/display.glb",
            }
        },
    )
    client = TestClient(create_app())
    response = client.get("/api/buildings", params={"path": str(pdf)})
    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["buildings"]] == ["事務所", "シティホテル"]
    assert body["buildings"][0]["status"] == "unread"
    assert body["buildings"][0]["scene_url"] is None
    assert body["buildings"][1]["status"] == "ready"
    assert body["buildings"][1]["glb_url"].endswith("/c17e107e1501/display.glb")
    assert called["model"] == 0


def test_editor_rerun_round_trip(tmp_path: Path):
    write_plan(1, tmp_path)
    client = TestClient(create_app())
    image = client.get("/api/page", params={"path": str(tmp_path / "plan.pdf")})
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    response = client.post(
        "/api/rerun",
        json={
            "input": str(tmp_path / "plan.pdf"),
            "output": str(tmp_path / "editor-out"),
            "corrections": {"operations": []},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["exit_code"] == 0
    assert body["rooms"] >= 5
    assert (tmp_path / "editor-out" / "corrections.json").is_file()
    assert (tmp_path / "editor-out" / "overlay.png").is_file()
