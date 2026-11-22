"""FastAPI app: API under /api (OpenAPI at /docs), React build served at /, health at /healthz."""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import admin, alerts, allocations, audit, auth, dashboard, imports, people, projects, requests
from app.config import get_settings
from app.db import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.jobs import scheduler

    if os.environ.get("RAH_DISABLE_SCHEDULER") != "1":
        scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Resource Allocation Hub", version="1.0.0", lifespan=lifespan, docs_url="/docs", openapi_url="/api/openapi.json")

for r in (auth, people, projects, allocations, requests, dashboard, alerts, imports, admin, audit):
    app.include_router(r.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    try:
        with engine.connect() as c:
            c.execute(text("select 1"))
        return {"status": "ok"}
    except Exception as e:  # pragma: no cover
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)


@app.get("/api/jobs", tags=["admin"])
def jobs():
    from app.jobs import scheduler

    return scheduler.next_runs()


def _static_dir() -> Path | None:
    for p in (get_settings().STATIC_DIR, "/app/static", str(Path(__file__).resolve().parents[2] / "frontend" / "dist")):
        if p and Path(p, "index.html").exists():
            return Path(p)
    return None


STATIC = _static_dir()
if STATIC is not None:
    if (STATIC / "assets").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        f = STATIC / path
        if path and f.is_file() and STATIC in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(STATIC / "index.html")
