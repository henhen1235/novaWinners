from pathlib import Path

from fastapi.testclient import TestClient

from editor.app import create_app
from tools.make_synthetic_plan import write_plan


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
