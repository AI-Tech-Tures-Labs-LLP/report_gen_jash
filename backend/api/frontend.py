"""Frontend serving: static mount + SPA entry points.

Serves the built React app (frontend-react/dist). The old vanilla frontend/
folder was deleted — this module now serves a Vite build, which means:

  • Hashed assets live in dist/assets and are mounted at /assets (that is the
    path Vite bakes into dist/index.html, so it must match exactly).
  • /report-view is a CLIENT-SIDE route — main.jsx inspects the URL and renders
    ReportPage. There is no report.html any more, so it returns index.html and
    lets the router take over. Same for any other SPA route.

FRONTEND_DIR points at the build output and is overridable via env var so the
Docker image can place it elsewhere.
"""

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# backend/api/frontend.py -> parents[2] is the repo root, then the Vite build dir.
FRONTEND_DIR = Path(
    os.getenv("FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "frontend-react" / "dist"))
)

router = APIRouter()


def mount_static(app):
    """Mount the Vite build's hashed assets (called from app.py).

    Mounted at /assets because that is the absolute path Vite writes into
    dist/index.html. Tolerates a missing dist/ so the API still boots in a
    backend-only dev setup (Vite serves the UI on :5173 in that case).
    """
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


def _index():
    """The SPA entry document — every client-side route resolves to this."""
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@router.get("/")
def serve_frontend():
    return _index()


@router.get("/report-view")
def serve_report_view():
    """Report viewer — a client-side route, so hand back the SPA shell.

    ReportPage reads ?id= from the URL and fetches the report from Mongo, so a
    hard refresh on this URL must still return index.html rather than 404.
    """
    return _index()
