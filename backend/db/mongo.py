"""MongoDB connection helper — uses motor (async) and pymongo (sync).

Reads MONGO_URI and MONGO_DB_NAME from core.config.
Anyone can change the connection string in .env to point to their own MongoDB
(local Compass, Atlas, Docker, etc.).
"""

from pymongo import MongoClient
from core.config import MONGO_URI, MONGO_DB_NAME

_client = None
_db = None


def get_mongo_client():
    """Return a singleton MongoClient."""
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI)
    return _client


def get_db():
    """Return the application database instance."""
    global _db
    if _db is None:
        _db = get_mongo_client()[MONGO_DB_NAME]
        _ensure_indexes(_db)
    return _db


def _ensure_indexes(db):
    """Create indexes for efficient queries (idempotent)."""
    # Users — unique email
    db.users.create_index("email", unique=True)
    # Conversations — per-user lookup
    db.conversations.create_index([("user_id", 1), ("updated_at", -1)])
    # Reports — per-user lookup
    db.reports.create_index([("user_id", 1), ("created_at", -1)])
