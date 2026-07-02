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


def add_turn(
    user_id: str,
    conv_id: str,
    question: str,
    answer: str,
    sql: str | None = None,
    query_result: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Append one Q/A turn to a conversation's LLM-memory log (Mongo).

    This is the AI's working memory for follow-up questions — kept in a `turns`
    array on the conversation doc, SEPARATE from the UI `messages` array (which the
    frontend owns). Stores the prior query_result so follow-ups can reference the
    actual numbers. Replaces the old Postgres chat_history.add_turn.
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    # Cap stored result rows to keep the doc small (memory only needs a sample).
    capped = (query_result or [])[:200] if query_result else None
    turn = {
        "question": question,
        "answer": answer,
        "sql": sql or "",
        "query_result": capped,
        "created_at": now,
    }
    db.conversations.update_one(
        {"user_id": user_id, "conv_id": conv_id},
        {
            "$push": {"turns": turn},
            "$set": {"updated_at": now},
            "$setOnInsert": {
                "user_id": user_id,
                "conv_id": conv_id,
                "created_at": now,
            },
        },
        upsert=True,
    )


def rename_conversation(user_id: str, conv_id: str, title: str) -> bool:
    """Update ONLY a conversation's title (cheap — no message reload/resave).

    Deliberately does NOT touch `updated_at`: the conversation list is sorted by
    `updated_at` (most-recent activity first), and a rename is NOT activity — only
    chatting in a conversation should bump it to the top. Renaming leaves its place.
    Returns True if a conversation was matched and updated, False otherwise."""
    db = get_db()
    result = db.conversations.update_one(
        {"user_id": user_id, "conv_id": conv_id},
        {"$set": {"title": title}},
    )
    return result.matched_count > 0


def get_recent_turns(user_id: str, conv_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Return the most recent `limit` turns for a conversation (oldest first).

    Used to give the LLM recent context for follow-ups. Replaces the old Postgres
    chat_history.get_recent_history. Returns [] if the conversation has no turns.
    """
    db = get_db()
    doc = db.conversations.find_one(
        {"user_id": user_id, "conv_id": conv_id},
        {"turns": {"$slice": -limit}, "_id": 0},
    )
    if not doc or not doc.get("turns"):
        return []
    return doc["turns"]


def get_full_turns(user_id: str, conv_id: str) -> List[Dict[str, Any]]:
    """Return ALL turns for a conversation (oldest first) — for the history sidebar."""
    db = get_db()
    doc = db.conversations.find_one(
        {"user_id": user_id, "conv_id": conv_id},
        {"turns": 1, "_id": 0},
    )
    if not doc or not doc.get("turns"):
        return []
    return doc["turns"]


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
