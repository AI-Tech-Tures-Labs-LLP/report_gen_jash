"""Frontend serving: static mount + index/report-view pages.

app.py now lives in backend/; the frontend is a sibling at the repo root (../frontend).
Overridable via the FRONTEND_DIR env var for flexible deploys.
"""

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# backend/api/frontend.py -> parents[2] is the repo root, then /frontend
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "frontend")))

router = APIRouter()


def mount_static(app):
    """Mount the /static assets directory on the app (called from app.py)."""
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@router.get("/")
def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@router.get("/report-view")
def serve_report_view():
    """Serve the standalone report viewer page (opens in new tab)."""
    return FileResponse(str(FRONTEND_DIR / "report.html"))
