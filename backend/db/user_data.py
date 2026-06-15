"""Per-user data persistence — conversations and reports in MongoDB.

All functions take a user_id (string) to scope data per-user.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from db.mongo import get_db


# ─── Conversations ──────────────────────────────────────────────────────────

def save_conversation(
    user_id: str,
    conv_id: str,
    title: str,
    messages: List[Dict[str, Any]],
) -> None:
    """Upsert a conversation (create or update)."""
    db = get_db()
    now = datetime.now(timezone.utc)
    db.conversations.update_one(
        {"user_id": user_id, "conv_id": conv_id},
        {
            "$set": {
                "title": title,
                "messages": messages,
                "updated_at": now,
            },
            "$setOnInsert": {
                "user_id": user_id,
                "conv_id": conv_id,
                "created_at": now,
            },
        },
        upsert=True,
    )


def get_user_conversations(user_id: str) -> List[Dict[str, Any]]:
    """List all conversations for a user (newest first), without full messages."""
    db = get_db()
    cursor = db.conversations.find(
        {"user_id": user_id},
        {"conv_id": 1, "title": 1, "updated_at": 1, "created_at": 1, "_id": 0},
    ).sort("updated_at", -1)
    return list(cursor)


def get_conversation(user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    """Get a full conversation including messages."""
    db = get_db()
    doc = db.conversations.find_one(
        {"user_id": user_id, "conv_id": conv_id},
        {"_id": 0},
    )
    return doc


def delete_conversation(user_id: str, conv_id: str) -> bool:
    """Delete a conversation and its associated reports."""
    db = get_db()
    result = db.conversations.delete_one({"user_id": user_id, "conv_id": conv_id})
    db.reports.delete_many({"user_id": user_id, "conv_id": conv_id})
    return result.deleted_count > 0


# ─── Reports ────────────────────────────────────────────────────────────────

def save_report(
    user_id: str,
    conv_id: str,
    report_id: str,
    question: str,
    report_data: Dict[str, Any],
) -> str:
    """Store a generated report. Returns the report_id."""
    db = get_db()
    now = datetime.now(timezone.utc)
    db.reports.update_one(
        {"user_id": user_id, "report_id": report_id},
        {
            "$set": {
                "conv_id": conv_id,
                "question": question,
                "report_data": report_data,
                "updated_at": now,
            },
            "$setOnInsert": {
                "user_id": user_id,
                "report_id": report_id,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return report_id


def get_user_reports(user_id: str) -> List[Dict[str, Any]]:
    """List all reports for a user (newest first), without full report_data."""
    db = get_db()
    cursor = db.reports.find(
        {"user_id": user_id},
        {"report_id": 1, "conv_id": 1, "question": 1, "created_at": 1, "_id": 0},
    ).sort("created_at", -1)
    return list(cursor)


def get_report(user_id: str, report_id: str) -> Optional[Dict[str, Any]]:
    """Get a full report including report_data."""
    db = get_db()
    doc = db.reports.find_one(
        {"user_id": user_id, "report_id": report_id},
        {"_id": 0},
    )
    return doc
