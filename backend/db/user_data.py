"""Per-user data persistence — conversations and reports in the app Postgres DB.

All functions take a user_id (string UUID) to scope data per-user. Function
signatures and return shapes match what the api/ layer already expects, so this
is a drop-in replacement for the previous MongoDB implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from db.app_connection import get_app_engine


# ─── Conversations ──────────────────────────────────────────────────────────

def save_conversation(
    user_id: str,
    conv_id: str,
    title: str,
    messages: List[Dict[str, Any]],
) -> None:
    """Upsert a conversation (create or update)."""
    import json
    sql = text("""
        INSERT INTO conversations (user_id, conv_id, title, messages, updated_at)
        VALUES (:user_id, :conv_id, :title, CAST(:messages AS jsonb), NOW())
        ON CONFLICT (user_id, conv_id) DO UPDATE
        SET title = EXCLUDED.title,
            messages = EXCLUDED.messages,
            updated_at = NOW()
    """)
    with get_app_engine().begin() as conn:
        conn.execute(sql, {
            "user_id": user_id,
            "conv_id": conv_id,
            "title": title,
            "messages": json.dumps(messages or []),
        })


def add_turn(
    user_id: str,
    conv_id: str,
    question: str,
    answer: str,
    sql: str | None = None,
    query_result: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Append one Q/A turn to a conversation's LLM-memory log.

    This is the AI's working memory for follow-up questions — rows in
    `conversation_turns`, SEPARATE from the UI `messages` array on the
    conversation row (which the frontend owns). Stores the prior query_result so
    follow-ups can reference the actual numbers.

    Ensures the parent conversation row exists first: turns carry a FK to users
    and are looked up by (user_id, conv_id), and the frontend can send a turn
    before it ever saves the conversation.
    """
    import json
    # Cap stored result rows to keep the payload small (memory only needs a sample).
    capped = (query_result or [])[:200] if query_result else None

    with get_app_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO conversations (user_id, conv_id, title, updated_at)
            VALUES (:user_id, :conv_id, 'New chat', NOW())
            ON CONFLICT (user_id, conv_id) DO UPDATE SET updated_at = NOW()
        """), {"user_id": user_id, "conv_id": conv_id})

        conn.execute(text("""
            INSERT INTO conversation_turns
                (user_id, conv_id, question, answer, sql, query_result)
            VALUES
                (:user_id, :conv_id, :question, :answer, :sql,
                 CAST(:query_result AS jsonb))
        """), {
            "user_id": user_id,
            "conv_id": conv_id,
            "question": question,
            "answer": answer,
            "sql": sql or "",
            "query_result": json.dumps(capped) if capped is not None else None,
        })


def rename_conversation(user_id: str, conv_id: str, title: str) -> bool:
    """Update ONLY a conversation's title (cheap — no message reload/resave).

    Deliberately does NOT touch `updated_at`: the conversation list is sorted by
    `updated_at` (most-recent activity first), and a rename is NOT activity — only
    chatting in a conversation should bump it to the top. Renaming leaves its place.
    Returns True if a conversation was matched and updated, False otherwise."""
    with get_app_engine().begin() as conn:
        result = conn.execute(text("""
            UPDATE conversations SET title = :title
            WHERE user_id = :user_id AND conv_id = :conv_id
        """), {"user_id": user_id, "conv_id": conv_id, "title": title})
        return result.rowcount > 0


def _turn_rows_to_dicts(rows) -> List[Dict[str, Any]]:
    """Map turn rows to the dict shape the AI-memory callers expect."""
    return [
        {
            "question": r.question,
            "answer": r.answer,
            "sql": r.sql,
            "query_result": r.query_result,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def get_recent_turns(user_id: str, conv_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Return the most recent `limit` turns for a conversation (oldest first).

    Used to give the LLM recent context for follow-ups. Returns [] if the
    conversation has no turns.
    """
    with get_app_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT question, answer, sql, query_result, created_at
            FROM conversation_turns
            WHERE user_id = :user_id AND conv_id = :conv_id
            ORDER BY turn_id DESC
            LIMIT :limit
        """), {"user_id": user_id, "conv_id": conv_id, "limit": limit}).fetchall()
    # Fetched newest-first for the LIMIT; callers want oldest-first.
    return _turn_rows_to_dicts(reversed(rows))


def get_full_turns(user_id: str, conv_id: str) -> List[Dict[str, Any]]:
    """Return ALL turns for a conversation (oldest first) — for the history sidebar."""
    with get_app_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT question, answer, sql, query_result, created_at
            FROM conversation_turns
            WHERE user_id = :user_id AND conv_id = :conv_id
            ORDER BY turn_id
        """), {"user_id": user_id, "conv_id": conv_id}).fetchall()
    return _turn_rows_to_dicts(rows)


def get_user_conversations(user_id: str) -> List[Dict[str, Any]]:
    """List all conversations for a user (newest first), without full messages."""
    with get_app_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT conv_id, title, updated_at, created_at
            FROM conversations
            WHERE user_id = :user_id
            ORDER BY updated_at DESC
        """), {"user_id": user_id}).fetchall()
    return [
        {
            "conv_id": r.conv_id,
            "title": r.title,
            "updated_at": r.updated_at,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def get_conversation(user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    """Get a full conversation including messages."""
    with get_app_engine().connect() as conn:
        row = conn.execute(text("""
            SELECT conv_id, title, messages, created_at, updated_at
            FROM conversations
            WHERE user_id = :user_id AND conv_id = :conv_id
        """), {"user_id": user_id, "conv_id": conv_id}).fetchone()
    if not row:
        return None
    return {
        "user_id": user_id,
        "conv_id": row.conv_id,
        "title": row.title,
        "messages": row.messages or [],
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def delete_conversation(user_id: str, conv_id: str) -> bool:
    """Delete a conversation and its associated turns and reports."""
    with get_app_engine().begin() as conn:
        result = conn.execute(text("""
            DELETE FROM conversations
            WHERE user_id = :user_id AND conv_id = :conv_id
        """), {"user_id": user_id, "conv_id": conv_id})
        conn.execute(text("""
            DELETE FROM conversation_turns
            WHERE user_id = :user_id AND conv_id = :conv_id
        """), {"user_id": user_id, "conv_id": conv_id})
        conn.execute(text("""
            DELETE FROM reports
            WHERE user_id = :user_id AND conv_id = :conv_id
        """), {"user_id": user_id, "conv_id": conv_id})
        return result.rowcount > 0


# ─── Reports ────────────────────────────────────────────────────────────────

def save_report(
    user_id: str,
    conv_id: str,
    report_id: str,
    question: str,
    report_data: Dict[str, Any],
) -> str:
    """Store a generated report. Returns the report_id."""
    import json
    with get_app_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO reports
                (user_id, report_id, conv_id, question, report_data, updated_at)
            VALUES
                (:user_id, :report_id, :conv_id, :question,
                 CAST(:report_data AS jsonb), NOW())
            ON CONFLICT (user_id, report_id) DO UPDATE
            SET conv_id = EXCLUDED.conv_id,
                question = EXCLUDED.question,
                report_data = EXCLUDED.report_data,
                updated_at = NOW()
        """), {
            "user_id": user_id,
            "report_id": report_id,
            "conv_id": conv_id,
            "question": question,
            "report_data": json.dumps(report_data or {}),
        })
    return report_id


def get_user_reports(user_id: str) -> List[Dict[str, Any]]:
    """List all reports for a user (newest first), without full report_data."""
    with get_app_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT report_id, conv_id, question, created_at
            FROM reports
            WHERE user_id = :user_id
            ORDER BY created_at DESC
        """), {"user_id": user_id}).fetchall()
    return [
        {
            "report_id": r.report_id,
            "conv_id": r.conv_id,
            "question": r.question,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def get_report(user_id: str, report_id: str) -> Optional[Dict[str, Any]]:
    """Get a full report including report_data."""
    with get_app_engine().connect() as conn:
        row = conn.execute(text("""
            SELECT report_id, conv_id, question, report_data, created_at, updated_at
            FROM reports
            WHERE user_id = :user_id AND report_id = :report_id
        """), {"user_id": user_id, "report_id": report_id}).fetchone()
    if not row:
        return None
    return {
        "user_id": user_id,
        "report_id": row.report_id,
        "conv_id": row.conv_id,
        "question": row.question,
        "report_data": row.report_data or {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
