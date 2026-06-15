"""Chat + smart-router endpoint: /ask.

`/ask` is the single chat entry point — both frontends call it. (A legacy
`/chat/stream` endpoint was removed; nothing called it.)
"""

import json as _json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.schemas import QuestionRequest

logger = logging.getLogger("api")
router = APIRouter()


@router.post("/ask")
def ask_endpoint(req: QuestionRequest):
    """Smart entry point for the main input box.

    Classifies the question's INTENT (backend-owned), then routes:
      - intent=report → run the full report pipeline, stream a 'report' result.
      - intent=chat   → run the fast chat answer, stream a 'chat' result (always
                        report_eligible=True so the UI always offers 'Generate Report').
    Streams SSE in BOTH cases so the frontend has one contract; the frontend branches
    on the final event's `mode` ("report" | "chat").
    """
    from services.claude_report_llm import (
        classify_query_intent, answer_chat_question, answer_conversational,
    )
    from db.memory import get_recent_history, add_turn

    def event_generator():
        # Both frontends send a conversation_id; "default" is only a safety net.
        # Warn if we hit it, so a missing id is visible rather than silently
        # bucketing unrelated turns together.
        if not req.conversation_id:
            logger.warning("ASK request with NO conversation_id — falling back to shared 'default' bucket")
        conversation_id = req.conversation_id or "default"
        logger.info("ASK request | conversation_id=%s | question=%s", conversation_id, req.question)

        # ── Step 1: classify intent (cheap Haiku) — GATEKEEPER: decides if we touch the DB at all,
        #            AND rates SQL complexity so we pick the cheapest SAFE model for the SQL step. ──
        yield f"data: {_json.dumps({'stage': 'routing', 'data': {'message': 'Understanding your request...'}})}\n\n"
        intent = classify_query_intent(req.question)
        mode = intent.get("mode", "data")
        complexity = intent.get("complexity", "complex")
        logger.info("ASK routed → %s / %s (%s)", mode, complexity, intent.get("reason", ""))
        yield f"data: {_json.dumps({'stage': 'routed', 'data': {'mode': mode, 'reason': intent.get('reason', '')}})}\n\n"

        # ── Step 1b: NON-DATA turns → answer directly, NO SQL, NO database ──
        if mode in ("conversational", "out_of_scope", "refuse"):
            reply = answer_conversational(req.question, mode=mode)
            # Persist so multi-turn context still flows, but no SQL/data.
            try:
                add_turn(conversation_id, req.question, reply, "", query_result=None)
            except Exception:
                pass
            yield f"data: {_json.dumps({'stage': 'complete', 'data': {'mode': 'chat', 'answer': reply, 'sql': '', 'data': [], 'insights': '', 'report_eligible': False, 'row_count': 0, 'non_data': True}})}\n\n"
            return

        # ── Step 2a: REPORT intent → full pipeline ──
        if mode == "report":
            try:
                import asyncio
                from services.enhanced_pipeline import EnhancedReportPipeline
                yield f"data: {_json.dumps({'stage': 'report_generating', 'data': {'message': 'Generating full report...'}})}\n\n"
                pipeline = EnhancedReportPipeline(
                    enable_logging=False, enable_signals=True, enable_optimization=True,
                )
                result = asyncio.run(pipeline.generate(
                    question=req.question, provider="claude", force_refresh=False,
                ))
                result["mode"] = "report"
                yield f"data: {_json.dumps({'stage': 'complete', 'data': _json.loads(_json.dumps(result, default=str))})}\n\n"
            except Exception as exc:
                logger.error("ASK report error: %s", exc)
                yield f"data: {_json.dumps({'stage': 'complete', 'data': {'mode': 'chat', 'answer': f'Report generation failed: {exc}', 'sql': '', 'data': [], 'insights': '', 'report_eligible': True, 'row_count': 0}})}\n\n"
            return

        # ── Step 2b: CHAT intent → fast answer (with history) ──
        history = get_recent_history(conversation_id, limit=5)
        if history:
            lines = ["You are in a multi-turn conversation. Here are the recent exchanges:"]
            for turn in history:
                lines.append(f"User: {turn['question']}")
                lines.append(f"Assistant: {turn['answer']}")
                # Include the PRIOR query results (compact) so follow-ups like
                # "break the top one down by month" can reference the actual
                # numbers/entities the previous answer was based on — not just
                # the prose. Cap rows + chars to keep the prompt small.
                prior_rows = turn.get("query_result")
                if prior_rows:
                    snippet = _json.dumps(prior_rows[:10], default=str)
                    if len(snippet) > 1500:
                        snippet = snippet[:1500] + "…(truncated)"
                    lines.append(f"(data behind that answer: {snippet})")
            lines.append(f"Now the user asks: {req.question}")
            question_with_context = "\n".join(lines)
        else:
            question_with_context = req.question

        # Pick the SQL model: Haiku for router-rated "simple" single-table facts (cost win),
        # Sonnet otherwise. Force Sonnet whenever there's conversation history — a follow-up
        # must reason over prior context/results, which the cheaper model handles less reliably.
        from core import config as _cfg
        use_haiku_sql = (complexity == "simple") and not history
        sql_model = _cfg.CLAUDE_HAIKU_MODEL if use_haiku_sql else None  # None → default Sonnet
        logger.info("ASK chat SQL model → %s", "haiku (simple)" if use_haiku_sql else "sonnet")

        try:
            for event in answer_chat_question(question_with_context, sql_model=sql_model):
                if event["stage"] == "complete":
                    result = event["data"]
                    add_turn(
                        conversation_id, req.question, result["answer"], result["sql"],
                        query_result=(result["data"][:200] if result.get("data") else None),
                    )
                    result["row_count"] = len(result.get("data") or [])
                    result["mode"] = "chat"
                    # Per Joel's goal: ALWAYS offer a report on the fast-chat path.
                    result["report_eligible"] = True
                    yield f"data: {_json.dumps(event, default=str)}\n\n"
                else:
                    yield f"data: {_json.dumps(event)}\n\n"
        except Exception as exc:
            logger.error("ASK chat error: %s", exc)
            yield f"data: {_json.dumps({'stage': 'complete', 'data': {'mode': 'chat', 'answer': f'An error occurred: {exc}', 'sql': '', 'data': [], 'insights': '', 'report_eligible': True, 'row_count': 0}})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
