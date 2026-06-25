"""FastAPI application — AI SQL Analyst API and frontend server.

Thin assembler: creates the app, configures CORS, warms caches at startup, and
includes the routers from the api/ layer. All route logic lives in api/*.
"""

import logging
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import chat, reports, meta, frontend, auth, conversations

logging.basicConfig(level=logging.WARNING, format="%(asctime)s  %(name)s  %(message)s")
logger = logging.getLogger("api")

app = FastAPI(title="AI SQL Analyst", version="2.0.0")


def _warm_caches():
    """Pre-build schema, relationship, and data-profile caches at startup.

    Runs in a background thread so the server starts instantly.
    Any request that arrives before the profile is ready gets the static
    business rules immediately (non-blocking) and the full profile on the
    next request.
    """
    try:
        logger.info("Cache warm-up — starting background pre-load...")
        from db.schema import format_schema
        from db.relationships import format_relationships
        import db.profiler as _profiler

        format_schema()
        logger.info("Cache warm-up — schema loaded")
        format_relationships()
        logger.info("Cache warm-up — relationships loaded")

        # Try to load from persistent DB cache first (milliseconds)
        loaded = _profiler.load_profile_from_db_cache()
        if loaded:
            logger.info("Cache warm-up — profile loaded from DB cache (instant)")
        else:
            # No DB cache yet (first ever deploy) — build from scratch
            logger.info("Cache warm-up — no DB cache found, building profile...")
            _profiler._do_build()
            logger.info("Cache warm-up — profile built and saved to DB")
    except Exception as exc:
        logger.warning("Cache warm-up failed (non-fatal): %s", exc)


# Kick off cache pre-loading as soon as the module is imported
threading.Thread(target=_warm_caches, daemon=True).start()

# ── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers (api/ layer) ──────────────────────────────────────────────────────
app.include_router(chat.router)
app.include_router(reports.router)
app.include_router(meta.router)
app.include_router(frontend.router)
app.include_router(auth.router)
app.include_router(conversations.router)

# Static assets mount (must be registered on the app, not a router)
frontend.mount_static(app)


# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run with backend/ as the import root so `import config`, `from ai.x`, `from db.x`
    # resolve (Option A). Works whether launched as `python backend/app.py` from the
    # repo root or `python app.py` from inside backend/.
    import sys as _sys
    import uvicorn
    _here = str(Path(__file__).resolve().parent)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, app_dir=_here)
