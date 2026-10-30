"""Read-only API over cached results. It never runs analysis or the LLM, so the demo works without a GPU."""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, f"{path.name} not found; run `handoff analyze` / `handoff kt` first")


def create_app(data_dir: Path) -> FastAPI:
    data_dir = Path(data_dir)
    app = FastAPI(title="Handoff")

    @app.get("/api/analysis")
    def analysis():
        return _load(data_dir / "analysis.json")

    @app.get("/api/kt/{dev_id}")
    def kt(dev_id: str):
        per_dev = data_dir / f"kt-{dev_id}.json"
        if per_dev.exists():
            return _load(per_dev)
        data = _load(data_dir / "kt.json")
        if data.get("dev") != dev_id:
            raise HTTPException(404, f"No KT generated for {dev_id}")
        return data

    @app.get("/", response_class=HTMLResponse)
    def index():
        page = WEB_DIR / "index.html"
        if page.exists():
            return FileResponse(page)
        return HTMLResponse("<p>Web UI not built yet. Try <a href='/api/analysis'>/api/analysis</a>.</p>")

    return app
