"""SQLAlchemy engine for the APP database (stacklogix_reportgeneration).

Deliberately separate from db/connection.py, which points at the read-only
analytics database (V2_inventory_management) that the LLM writes SQL against.

Nothing in the LLM query path may import this module: users, conversations and
reports live here, and keeping the two engines apart is what stops a generated
SELECT from ever reaching a password hash.
"""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from core import config

_engine: Engine | None = None


def get_app_engine() -> Engine:
    """Return a singleton SQLAlchemy engine for the app database."""
    global _engine
    if _engine is None:
        _engine = create_engine(config.APP_DATABASE_URL, pool_pre_ping=True)
    return _engine


def get_app_connection():
    """Return a new app-database connection (context-manager)."""
    return get_app_engine().connect()
