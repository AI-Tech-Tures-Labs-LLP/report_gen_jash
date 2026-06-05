"""Chat + smart-router endpoints: /chat/stream and /ask."""

import json as _json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.schemas import QuestionRequest

logger = logging.getLogger("api")
router = APIRouter()


@router.post("/chat/stream")
def chat_stream_endpoint(req: QuestionRequest):
    """Stream chat progress via Server-Sent Events (SSE).

    Runs on Claude via the shared SQL Agent (same brain as report generation).
    Sends real-time progress updates as each stage completes, then the final
    result as the last event.
    """
    from services.report_generator import classify_intent
    from services.claude_report_llm import answer_chat_question
    from db.memory import get_recent_history, add_turn

    def event_generator():
        logger.info(
            "STREAM request | conversation_id=%s | question=%s",
            req.conversation_id or "default",
            req.question,
        )

        intent = classify_intent(req.question)
        report_eligible_by_intent = intent == "report"
        conversation_id = req.conversation_id or "default"
        history = get_recent_history(conversation_id, limit=5)

        if history:
            history_lines = ["You are in a multi-turn conversation. Here are the recent exchanges:"]
            for turn in history:
                history_lines.append(f"User: {turn['question']}")
                history_lines.append(f"Assistant: {turn['answer']}")
            history_lines.append(f"Now the user asks: {req.question}")
            question_with_context = "\n".join(history_lines)
        else:
            question_with_context = req.question

        try:
            for event in answer_chat_question(question_with_context):
                if event["stage"] == "complete":
                    result = event["data"]
                    # Persist this turn
                    add_turn(
                        conversation_id,
                        req.question,
                        result["answer"],
                        result["sql"],
                        query_result=(result["data"][:200] if result.get("data") else None),
                    )
                    data_rows = len(result.get("data") or [])
                    report_eligible = report_eligible_by_intent or data_rows >= 3
                    result["report_eligible"] = report_eligible
                    result["row_count"] = data_rows
                    result["mode"] = "chat"
                    yield f"data: {_json.dumps(event, default=str)}\n\n"
                else:
                    yield f"data: {_json.dumps(event)}\n\n"
        except Exception as exc:
            logger.error("STREAM error: %s", exc)
            error_event = {
                "stage": "complete",
                "data": {
                    "sql": "",
                    "data": [],
                    "answer": f"An error occurred: {str(exc)}",
                    "insights": "",
                    "report_eligible": False,
                    "row_count": 0,
                    "mode": "chat",
                },
            }
            yield f"data: {_json.dumps(error_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
    from services.claude_report_llm import classify_query_intent, answer_chat_question
    from services.report_generator import classify_intent  # noqa: F401 (kept for parity)
    from db.memory import get_recent_history, add_turn

    def event_generator():
        conversation_id = req.conversation_id or "default"
        logger.info("ASK request | conversation_id=%s | question=%s", conversation_id, req.question)

        # ── Step 1: classify intent (cheap Haiku) ──
        yield f"data: {_json.dumps({'stage': 'routing', 'data': {'message': 'Understanding your request...'}})}\n\n"
        intent = classify_query_intent(req.question)
        mode = intent.get("mode", "chat")
        logger.info("ASK routed → %s (%s)", mode, intent.get("reason", ""))
        yield f"data: {_json.dumps({'stage': 'routed', 'data': {'mode': mode, 'reason': intent.get('reason', '')}})}\n\n"

        # ── Step 2a: REPORT intent → full pipeline ──
        if mode == "report":
            try:
                import asyncio
                from services.enhanced_pipeline import EnhancedReportPipeline
                yield f"data: {_json.dumps({'stage': 'report_generating', 'data': {'message': 'Generating full report...'}})}\n\n"
                pipeline = EnhancedReportPipeline(
                    enable_logging=True, enable_signals=True, enable_optimization=True,
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
            lines.append(f"Now the user asks: {req.question}")
            question_with_context = "\n".join(lines)
        else:
            question_with_context = req.question

        try:
            for event in answer_chat_question(question_with_context):
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
