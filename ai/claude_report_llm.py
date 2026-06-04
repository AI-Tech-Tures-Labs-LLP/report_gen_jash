"""Claude-backed LLM functions for report modification and chat SQL answering.

This module replaces the legacy DSPy/Groq signatures that previously powered
the `/report/modify` and `/chat/stream` endpoints. Both now run on Claude via
the shared `ClaudeClient`, reusing the same SQL Agent brain that drives report
generation — so chat, reports, and modifications all share one model and one
SQL-generation approach.

Functions:
- `modify_report()`   — replaces the DSPy `ReportModification` signature.
- `answer_chat_question()` — replaces the DSPy chat pipeline (analyze→SQL→
  execute→interpret), built on the existing Claude SQL Agent.
"""

import json
import logging
import time as _time
from typing import Any, Iterator

from ai.claude_client import ClaudeClient
from ai.claude_prompts import get_sql_agent_system
from ai.claude_tools import SQL_AGENT_TOOLS, TOOL_HANDLERS
from db.schema import format_schema
from db.relationships import format_relationships
from db.profiler import get_data_profile
from db.executor import execute_sql

logger = logging.getLogger(__name__)


# ── Report modification (replaces DSPy ReportModification) ───────────────────

_MODIFY_SYSTEM = """You are a report editor. Given an existing report JSON and a user
modification command, produce the COMPLETE updated report JSON with ONLY the
requested changes applied.

RULES:
- PRESERVE everything NOT mentioned in the modification command
- Copy ALL unchanged KPIs, charts, table, insights EXACTLY as-is
- Maintain the EXACT same JSON structure
- When changing chart type: keep the SAME sql, title, explanation — ONLY change the "type" field
- When adding new charts/KPIs, write valid PostgreSQL SQL using proper joins based on the schema
- Do NOT use polarArea, radar, or scatter chart types
- Each KPI SQL must return exactly ONE row with ONE value column
- Each chart SQL must return at least 2 columns (label + value)
- Output ONLY the complete JSON — no markdown, no code fences

The user may reference items by:
- CHART TITLE: find chart whose title best matches
- CHART TYPE: find chart with that type
- KPI LABEL: find KPI whose label best matches
- POSITION: "first chart", "last KPI"
- GENERAL: "add a new KPI", "add another chart"

SQL RULES:
- Use ONLY columns that exist on each table (check the schema)
- Follow proper join paths between tables
- Never reference columns on wrong tables"""


def modify_report(current_report: str, modification: str, schema_info: str,
                  client: ClaudeClient | None = None) -> str:
    """Apply a natural-language modification to a report's structure.

    Returns the updated report JSON as a raw string (the caller parses it,
    same contract as the old DSPy `result.updated_report_json`).
    """
    client = client or ClaudeClient()
    user_message = (
        f"DATABASE SCHEMA:\n{schema_info}\n\n"
        f"CURRENT REPORT JSON (structure + SQL only, no data):\n{current_report}\n\n"
        f"MODIFICATION COMMAND: {modification}\n\n"
        f"Output ONLY the complete updated report JSON."
    )
    return client.call_agent(
        system_prompt=_MODIFY_SYSTEM,
        user_message=user_message,
        agent_name="Report Modifier",
        use_cache=True,
    )


# ── Chat SQL answering (replaces DSPy chat pipeline) ─────────────────────────

_CHAT_INTERPRET_SYSTEM = """You are a data analyst. Given a user question, the SQL
that was run, and the query results (JSON), write a clear, concise answer.

Output a JSON object with exactly two keys:
- "answer": a plain-language explanation of what the results show (2-4 sentences)
- "insights": 3-5 short analytic bullet points as a single string (newline-separated)

Output ONLY the JSON object — no markdown, no code fences."""


def answer_chat_question(question: str, client: ClaudeClient | None = None
                         ) -> Iterator[dict]:
    """Answer a chat question by generating + executing SQL, then interpreting.

    Yields staged progress events for SSE streaming, mirroring the shape the
    frontend expects:
        {"stage": "analyze",   "data": {...}}
        {"stage": "sql",       "data": {"sql": "..."}}
        {"stage": "execute",   "data": {"row_count": N}}
        {"stage": "interpret", "data": {...}}
        {"stage": "complete",  "data": {"sql", "data", "answer", "insights"}}

    Built on the same Claude SQL Agent that powers report generation, so chat
    and reports share one SQL-generation brain.
    """
    client = client or ClaudeClient()
    client.reset_usage()  # clear telemetry for this chat turn
    _chat_start = _time.time()

    # ── Stage 1: SQL Agent generates + validates + executes SQL via tools ──
    yield {"stage": "analyze", "data": {"message": "Understanding the question..."}}

    schema_str = format_schema()
    rels_str = format_relationships()
    profile_str = get_data_profile()
    sql_system = get_sql_agent_system(schema_str, rels_str, profile_str)

    yield {"stage": "sql", "data": {"message": "Generating SQL..."}}

    sql_response = client.call_agent(
        system_prompt=sql_system,
        user_message=(
            f"Answer this question by writing and executing PostgreSQL.\n\n"
            f"QUESTION: {question}\n\n"
            f"Use the validate_sql_query and execute_sql_query tools. After you "
            f"have the results, respond with a JSON object containing the final "
            f'"sql" you ran and the "data" rows (as returned by the tool). '
            f"Output ONLY that JSON object."
        ),
        tools=SQL_AGENT_TOOLS,
        tool_handlers=TOOL_HANDLERS,
        agent_name="Chat SQL Agent",
        use_cache=True,
    )

    sql = ""
    rows: list[dict] = []
    try:
        parsed = client.extract_json(sql_response)
        sql = parsed.get("sql", "") or ""
        rows = parsed.get("data") or []
    except (json.JSONDecodeError, ValueError):
        # Fallback: the agent returned prose; treat the whole thing as the answer
        logger.warning("Chat SQL agent returned non-JSON; using text fallback")

    # If the agent reported SQL but no rows (e.g. it didn't echo data), re-run it.
    if sql and not rows:
        result = execute_sql(sql)
        if result.get("success"):
            rows = result.get("data") or []

    yield {"stage": "execute", "data": {"row_count": len(rows)}}

    # ── Stage 2: Interpret the results into answer + insights ──
    yield {"stage": "interpret", "data": {"message": "Interpreting results..."}}

    answer = ""
    insights = ""
    if sql:
        interpret_response = client.call_agent(
            system_prompt=_CHAT_INTERPRET_SYSTEM,
            user_message=(
                f"QUESTION: {question}\n\n"
                f"SQL RUN: {sql}\n\n"
                f"RESULTS (JSON, up to 50 rows): {json.dumps(rows[:50], default=str)}"
            ),
            agent_name="Chat Interpreter",
            use_cache=True,
        )
        try:
            interp = client.extract_json(interpret_response)
            answer = interp.get("answer", "") or ""
            insights = interp.get("insights", "") or ""
        except (json.JSONDecodeError, ValueError):
            answer = interpret_response.strip()
    else:
        # No SQL was produced — surface the agent's text response as the answer
        answer = sql_response.strip() if sql_response else "I couldn't answer that question."

    from ai.claude_multi_agent import _build_metrics
    metrics = _build_metrics(client.usage_log, _time.time() - _chat_start)

    yield {
        "stage": "complete",
        "data": {
            "sql": sql,
            "data": rows,
            "answer": answer,
            "insights": insights,
            "metrics": metrics,
        },
    }
