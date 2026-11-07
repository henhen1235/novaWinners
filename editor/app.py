"""Correction editor API.

Pixel operations are stored in corrections.json and replayed by the pipeline.
``POST /api/generate`` reads an upload with the vision floor reader, then falls
back to the vector pipeline when that call returns nothing.
``POST /api/rerun`` runs with ``--no-llm``.

    uvicorn editor.app:app --reload
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

import cv2
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ingest_blueprint.area_plans import read_area_plans
from ingest_blueprint.llm_floor import list_buildings, read_building
from ingest_blueprint.loader import load
from ingest_blueprint.pipeline import run

STATIC = Path(__file__).resolve().parent / "static"
RUNS = Path(__file__).resolve().parents[1] / "out" / "web"
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".dxf"}
ARTIFACTS = {"scene.json", "display.glb", "overlay.png", "navmesh_debug.glb"}
MAX_UPLOAD_BYTES = 40 * 1024 * 1024


class RerunRequest(BaseModel):
    input: str
    output: str
    corrections: dict = Field(default_factory=lambda: {"operations": []})


_UNNAMED = {"", "plan", "Building"}
_CATALOG_CACHE: dict[str, tuple[int, int, list[dict]]] = {}


def _generate_body(
    run_id: str,
    scene: dict,
    code: int,
    building: str,
    floors: int,
    reader: str,
    note: str,
    buildings: list[dict] | None = None,
) -> dict:
    body = {
        "run_id": run_id,
        "exit_code": code,
        "rooms": len(scene["rooms"]),
        "walls": len(scene["walls"]),
        "doors": len(scene["doors"]),
        "exits": len(scene["exits"]),
        "warnings": scene["warnings"],
        "scene_url": f"/api/runs/{run_id}/scene.json",
        "glb_url": f"/api/runs/{run_id}/display.glb",
        "overlay_url": f"/api/runs/{run_id}/overlay.png",
        "building": building,
        "floors": floors,
        "reader": reader,
        "buildings": buildings or [],
    }
    if note:
        body["note"] = note
    return body


def _catalog_for(source: Path) -> list[dict]:
    """Building list from page titles only. Cached by size and mtime."""

    if source.suffix.lower() != ".pdf" or not source.is_file():
        return []
    stat = source.stat()
    key = str(source.resolve())
    hit = _CATALOG_CACHE.get(key)
    stamp = (stat.st_mtime_ns, stat.st_size)
    if hit and hit[0] == stamp[0] and hit[1] == stamp[1]:
        return hit[2]
    try:
        catalog = list_buildings(source)
    except Exception:
        catalog = []
    _CATALOG_CACHE[key] = (stamp[0], stamp[1], catalog)
    return catalog


def _record_building(run_dir: Path, building: str, floors: int) -> None:
    """Remember which building a finished run displays, so a later tab can find it.

    The scene's own name has to match. A vector fallback named ``plan`` must not
    claim a building it did not read.
    """

    scene_path = run_dir / "scene.json"
    if not scene_path.is_file() or not (run_dir / "display.glb").is_file():
        return
    scene_name, scene_floors = _scene_meta(scene_path)
    name = building.strip()
    if name in _UNNAMED or scene_name != name:
        return
    payload = {
        "name": name,
        "floors": scene_floors or floors,
        "scene_url": f"/api/runs/{run_dir.name}/scene.json",
        "glb_url": f"/api/runs/{run_dir.name}/display.glb",
    }
    (run_dir / "building.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _scene_meta(path: Path) -> tuple[str, int]:
    try:
        scene = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", 0
    name = str(scene.get("meta", {}).get("name") or "").strip()
    levels = scene.get("levels")
    return name, len(levels) if isinstance(levels, list) else 0


def _scene_index() -> dict[str, dict]:
    """Map a building name to the run that already has a model."""

    found: dict[str, dict] = {}
    if not RUNS.is_dir():
        return found
    for run_dir in RUNS.iterdir():
        if not run_dir.is_dir() or not re.fullmatch(r"[0-9a-f]{12}", run_dir.name):
            continue
        scene_path = run_dir / "scene.json"
        if not scene_path.is_file() or not (run_dir / "display.glb").is_file():
            continue
        recorded = ""
        recorded_floors = 0
        record_path = run_dir / "building.json"
        if record_path.is_file():
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = {}
            recorded = str(record.get("name") or "").strip()
            recorded_floors = int(record.get("floors") or 0)
        size = scene_path.stat().st_size
        scene_name, scene_floors = ("", 0)
        if size <= 2_000_000:
            scene_name, scene_floors = _scene_meta(scene_path)
        if scene_name and scene_name not in _UNNAMED:
            name = scene_name
            floors = scene_floors or recorded_floors
        elif size > 2_000_000 and recorded and recorded not in _UNNAMED:
            name = recorded
            floors = recorded_floors
        else:
            continue
        entry = {
            "run_id": run_dir.name,
            "floors": floors,
            "scene_url": f"/api/runs/{run_dir.name}/scene.json",
            "glb_url": f"/api/runs/{run_dir.name}/display.glb",
        }
        current = found.get(name)
        if current is None or (entry["floors"], run_dir.stat().st_mtime) >= (current["floors"], current["mtime"]):
            entry["mtime"] = run_dir.stat().st_mtime
            found[name] = entry
    for entry in found.values():
        entry.pop("mtime", None)
    return found


def _annotate_buildings(buildings: list[dict], scenes: dict[str, dict]) -> list[dict]:
    annotated = []
    for building in buildings:
        scene = scenes.get(building["name"])
        row = dict(building)
        if scene:
            row["run_id"] = scene["run_id"]
            row["scene_url"] = scene["scene_url"]
            row["glb_url"] = scene["glb_url"]
            row["status"] = "ready"
        else:
            row["run_id"] = None
            row["scene_url"] = None
            row["glb_url"] = None
            row["status"] = "unread"
        annotated.append(row)
    return annotated


def _discover_plan() -> Path | None:
    """Pick the multi-building PDF already saved under a run directory."""

    if not RUNS.is_dir():
        return None
    candidates = [path for path in RUNS.glob("*/plan.pdf") if path.is_file() and path.stat().st_size >= 1_000_000]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    best: Path | None = None
    best_count = 0
    for path in candidates:
        count = len(_catalog_for(path))
        if count > best_count:
            best = path
            best_count = count
        if count >= 2 and best is path:
            return path
    return best


def _resolve_plan(path: str, run: str) -> Path:
    if path:
        candidate = Path(path)
        if candidate.is_file() and candidate.suffix.lower() == ".pdf":
            return candidate
        raise HTTPException(404, "PDF not found")
    if run and re.fullmatch(r"[0-9a-f]{12}", run):
        candidate = RUNS / run / "plan.pdf"
        if candidate.is_file():
            return candidate
    discovered = _discover_plan()
    if discovered is None:
        raise HTTPException(404, "No blueprint PDF was found")
    return discovered


def create_app() -> FastAPI:
    app = FastAPI(title="Blueprint corrections")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/buildings")
    def buildings(path: str = "", run: str = "") -> dict:
        """List buildings from PDF titles. Does not call the vision model."""

        source = _resolve_plan(path, run)
        catalog = _annotate_buildings(_catalog_for(source), _scene_index())
        return {"pdf": str(source), "buildings": catalog}

    @app.get("/api/page")
    def page(path: str, index: int = 0) -> Response:
        pages = load(path)
        if index < 0 or index >= len(pages):
            raise HTTPException(404, "page not found")
        raster = pages[index].raster
        ok, encoded = cv2.imencode(".png", cv2.cvtColor(raster, cv2.COLOR_RGB2BGR))
        if not ok:
            raise HTTPException(500, "could not encode page")
        return Response(content=encoded.tobytes(), media_type="image/png")

    @app.post("/api/generate")
    async def generate(file: UploadFile = File(...)) -> dict:
        """Run an uploaded floor plan through the pipeline and return the 3D model."""

        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(400, "Upload a PDF, PNG, JPG, TIFF, or DXF floor plan")
        payload = await file.read()
        if not payload:
            raise HTTPException(400, "That file is empty")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File is larger than 40 MB")
        run_id = uuid4().hex[:12]
        run_dir = RUNS / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        source = run_dir / f"plan{suffix}"
        source.write_bytes(payload)
        built = None
        try:
            built = read_area_plans(source, run_dir, label=file.filename or "")
        except Exception:
            built = None
        if not built:
            try:
                built = read_building(source, run_dir)
            except Exception:
                built = None
        if not built:
            try:
                scene, code = run(source, run_dir, no_llm=True)
            except Exception as exc:
                raise HTTPException(422, f"Could not read that plan: {exc}") from exc
            building = scene.get("meta", {}).get("name") or source.stem
            floors = len(scene["levels"])
            _record_building(run_dir, building, floors)
            return _generate_body(
                run_id,
                scene,
                code,
                building=building,
                floors=floors,
                reader="vector",
                note="Vector pipeline used for this plan.",
                buildings=_annotate_buildings(_catalog_for(source), _scene_index()),
            )
        _record_building(run_dir, built["building"], built["floors"])
        return _generate_body(
            run_id,
            built["scene"],
            built["exit_code"],
            building=built["building"],
            floors=built["floors"],
            reader=built.get("reader", "llm"),
            note="Rooms come from the areas printed on the sheet." if built.get("reader") == "area" else "",
            buildings=_annotate_buildings(_catalog_for(source), _scene_index()),
        )

    @app.get("/api/runs/{run_id}/{name}")
    def artifact(run_id: str, name: str) -> FileResponse:
        if not re.fullmatch(r"[0-9a-f]{12}", run_id) or name not in ARTIFACTS:
            raise HTTPException(404, "result not found")
        path = RUNS / run_id / name
        if not path.is_file():
            raise HTTPException(404, "result not found")
        return FileResponse(path)

    @app.post("/api/rerun")
    def rerun(body: RerunRequest) -> dict:
        out = Path(body.output)
        out.mkdir(parents=True, exist_ok=True)
        corrections_path = out / "corrections.json"
        corrections_path.write_text(json.dumps(body.corrections, indent=2) + "\n", encoding="utf-8")
        scene, code = run(body.input, out, no_llm=True, corrections_path=corrections_path)
        return {
            "exit_code": code,
            "rooms": len(scene["rooms"]),
            "doors": len(scene["doors"]),
            "exits": len(scene["exits"]),
            "warnings": scene["warnings"],
            "overlay": "overlay.png",
        }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/editor.js")
    def script() -> FileResponse:
        return FileResponse(STATIC / "editor.js")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("editor.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
