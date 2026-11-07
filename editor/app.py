"""Correction editor API.

Pixel operations are stored in corrections.json and replayed by the pipeline.
``POST /api/rerun`` runs with ``--no-llm``.

    uvicorn editor.app:app --reload
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ingest_blueprint.loader import load
from ingest_blueprint.pipeline import run

STATIC = Path(__file__).resolve().parent / "static"


class RerunRequest(BaseModel):
    input: str
    output: str
    corrections: dict = Field(default_factory=lambda: {"operations": []})


def create_app() -> FastAPI:
    app = FastAPI(title="Blueprint corrections")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
