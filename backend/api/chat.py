"""Chat + smart-router endpoint: /ask.

`/ask` is the single chat entry point — both frontends call it. (A legacy
`/chat/stream` endpoint was removed; nothing called it.)
"""

import asyncio
import concurrent.futures as _futures
import json as _json
import logging
import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from api.auth import get_current_user
from api.schemas import QuestionRequest

logger = logging.getLogger("api")
router = APIRouter()


@router.post("/ask")
def ask_endpoint(req: QuestionRequest, request: Request, current_user: dict = Depends(get_current_user)):
    """Smart entry point for the main input box.

    Classifies the question's INTENT (backend-owned), then routes:
      - intent=report → run the full report pipeline, stream a 'report' result.
      - intent=chat   → run the fast chat answer, stream a 'chat' result (always
                        report_eligible=True so the UI always offers 'Generate Report').
    Streams SSE in BOTH cases so the frontend has one contract; the frontend branches
    on the final event's `mode` ("report" | "chat").

    LOGIN-ONLY: requires a valid auth token. Conversation memory is stored per-user
    in Mongo (db.user_data), keyed by (user_id, conversation_id) — the central business
    Postgres is no longer touched for chat history. Auth is resolved HERE (not inside
    the generator) so a bad token returns a clean 401 before the SSE stream starts.
    """
    from services.claude_report_llm import (
        classify_query_intent, answer_chat_question, answer_conversational,
    )
    from db.user_data import get_recent_turns, add_turn

    user_id = current_user["user_id"]

    async def event_generator():
        # conversation_id scopes the thread WITHIN this user. Default per-user bucket
        # if the client omits one (still isolated per user, never shared across users).
        conversation_id = req.conversation_id or "default"
        logger.info("ASK request | user=%s conv=%s | question=%s", user_id, conversation_id, req.question)

        # ── Step 1: history fetch + intent classify + schema warm — all in parallel. ──
        # Three fully independent operations:
        #   • history fetch  — MongoDB read, needs only (user_id, conv_id)
        #   • intent classify — Haiku API call, needs only the question text
        #   • schema warm    — DB reads that populate module-level caches
        # Running all three concurrently hides the ~100ms Mongo fetch and the
        # ~1.5s classifier behind the schema load that would happen anyway.
        # Everything here is synchronous work, so each is dispatched via
        # asyncio.to_thread — this function is now `async def` (needed so
        # request.is_disconnected() below is valid on the SAME event loop that's
        # actually servicing this connection), and blocking calls made directly
        # in an async function would freeze that loop instead of yielding to it.
        from db.schema import format_schema as _warm_schema
        from db.relationships import format_relationships as _warm_rels
        from db.profiler import get_data_profile as _warm_profile

        yield f"data: {_json.dumps({'stage': 'routing', 'data': {'message': 'Understanding your request...'}})}\n\n"

        history, intent, _ = await asyncio.gather(
            asyncio.to_thread(get_recent_turns, user_id, conversation_id, 5),
            # Intent classifier needs router_context from history — but for the FIRST
            # message of a session history is empty, so we can classify with "" safely.
            # For follow-ups the classifier still works (it resolves pronouns from context),
            # and history arrives before answer_chat_question needs it.
            asyncio.to_thread(classify_query_intent, req.question, None, ""),
            asyncio.to_thread(lambda: (_warm_schema(), _warm_rels(), _warm_profile())),
        )

        # Build compact router context from the now-fetched history (used for logging only;
        # the classifier already ran — on follow-ups the full history is injected into the
        # SQL agent prompt below, which is where it matters most for accuracy).
        router_context = ""
        if history:
            router_context = "\n".join(
                f"User: {t['question']}\nAssistant: {t['answer']}" for t in history
            )
        mode = intent.get("mode", "data")
        complexity = intent.get("complexity", "complex")
        logger.info("ASK routed → %s / %s (%s)", mode, complexity, intent.get("reason", ""))
        # `complexity` is included so the frontend can show a derived, fully-templated
        # "reasoning" line (focused lookup vs broader analysis). It is process framing
        # only — never a data claim — so it can't contradict the final answer.
        yield f"data: {_json.dumps({'stage': 'routed', 'data': {'mode': mode, 'complexity': complexity, 'reason': intent.get('reason', '')}})}\n\n"

        # ── Step 1b: NON-DATA turns → answer directly, NO SQL, NO database ──
        if mode in ("conversational", "out_of_scope", "refuse"):
            reply = await asyncio.to_thread(answer_conversational, req.question, mode=mode)
            # Persist so multi-turn context still flows, but no SQL/data.
            try:
                await asyncio.to_thread(add_turn, user_id, conversation_id, req.question, reply, "", query_result=None)
            except Exception:
                pass
            yield f"data: {_json.dumps({'stage': 'complete', 'data': {'mode': 'chat', 'answer': reply, 'sql': '', 'data': [], 'insights': '', 'report_eligible': False, 'row_count': 0, 'non_data': True}})}\n\n"
            return

        # ── Step 2a: REPORT intent → full pipeline ──
        if mode == "report":
            try:
                from services.enhanced_pipeline import EnhancedReportPipeline
                yield f"data: {_json.dumps({'stage': 'report_generating', 'data': {'message': 'Generating full report...'}})}\n\n"
                pipeline = EnhancedReportPipeline(
                    enable_logging=False, enable_signals=True, enable_optimization=True,
                )

                # The pipeline itself runs in a worker thread (see
                # EnhancedReportPipeline.generate) and is only checked for
                # cancellation BETWEEN agent stages via should_stop — an in-flight
                # LLM call always finishes, since the Anthropic SDK calls here are
                # synchronous with no interruption point. `stop_event` is a
                # threading.Event so it's safe to read from the worker thread
                # while this coroutine sets it from the event loop.
                stop_event = threading.Event()

                pipeline_task = asyncio.ensure_future(pipeline.generate(
                    question=req.question, provider="claude", force_refresh=False,
                    should_stop=stop_event.is_set,
                ))

                async def _poll_disconnect():
                    while not await request.is_disconnected():
                        await asyncio.sleep(0.5)

                disconnect_task = asyncio.ensure_future(_poll_disconnect())
                done, pending = await asyncio.wait(
                    {pipeline_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED,
                )

                if pipeline_task in done:
                    disconnect_task.cancel()
                    result = pipeline_task.result()
                else:
                    # Client disconnected first — signal the pipeline to stop at
                    # the next stage boundary and wait for it to wind down
                    # cleanly (the in-flight agent call still finishes; only the
                    # NEXT agent is skipped).
                    logger.info(
                        "ASK report | client disconnected (user=%s conv=%s) — signalling "
                        "pipeline to stop before its next agent stage",
                        user_id, conversation_id,
                    )
                    stop_event.set()
                    result = await pipeline_task

                result["mode"] = "report"
                if result.get("status") == "cancelled":
                    logger.info(
                        "ASK report | generation CANCELLED (user=%s conv=%s) — question: %s",
                        user_id, conversation_id, req.question[:120],
                    )
                    # The client already disconnected, so nothing is listening on
                    # this stream — yielding is harmless but won't be received.
                    yield f"data: {_json.dumps({'stage': 'cancelled', 'data': _json.loads(_json.dumps(result, default=str))})}\n\n"
                else:
                    yield f"data: {_json.dumps({'stage': 'complete', 'data': _json.loads(_json.dumps(result, default=str))})}\n\n"
            except Exception as exc:
                logger.error("ASK report error: %s", exc)
                yield f"data: {_json.dumps({'stage': 'complete', 'data': {'mode': 'chat', 'answer': f'Report generation failed: {exc}', 'sql': '', 'data': [], 'insights': '', 'report_eligible': True, 'row_count': 0}})}\n\n"
            return

        # ── Step 2b: CHAT intent → fast answer (history already fetched above) ──
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
                    result["row_count"] = len(result.get("data") or [])
                    result["mode"] = "chat"
                    # Per Joel's goal: ALWAYS offer a report on the fast-chat path.
                    result["report_eligible"] = True
                    # Yield the final event FIRST so the user gets their answer
                    # immediately, then persist to Mongo in a background thread.
                    # History is only needed on the NEXT question — the ~100ms
                    # write latency is fully hidden from the user.
                    yield f"data: {_json.dumps(event, default=str)}\n\n"
                    _futures.ThreadPoolExecutor(max_workers=1).submit(
                        add_turn,
                        user_id, conversation_id, req.question,
                        result["answer"], result["sql"],
                        result["data"][:200] if result.get("data") else None,
                    )
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
