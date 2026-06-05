"""Conversation history endpoints: /history and /history/{turn_id}/*."""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/history")
def history_endpoint(conversation_id: str = "default"):
    from db.memory import get_full_history
    return get_full_history(conversation_id)


@router.delete("/history/{turn_id}")
def delete_turn_endpoint(turn_id: int):
    from db.memory import delete_turn
    delete_turn(turn_id)
    return {"ok": True}


@router.get("/history/{turn_id}/sql")
def history_sql_endpoint(turn_id: int):
    """Return just the SQL query for a specific history turn."""
    from db.memory import get_turn_by_id
    turn = get_turn_by_id(turn_id)
    if not turn:
        raise HTTPException(status_code=404, detail="Turn not found")
    return {"turn_id": turn_id, "sql": turn.get("sql_query")}


@router.get("/history/{turn_id}/result")
def history_result_endpoint(turn_id: int):
    """Return just the query result data for a specific history turn."""
    from db.memory import get_turn_by_id
    turn = get_turn_by_id(turn_id)
    if not turn:
        raise HTTPException(status_code=404, detail="Turn not found")
    data = turn.get("query_result") or []
    return {"turn_id": turn_id, "data": data, "row_count": len(data)}


@router.get("/history/{turn_id}/answer")
def history_answer_endpoint(turn_id: int):
    """Return just the AI answer/explanation for a specific history turn."""
    from db.memory import get_turn_by_id
    turn = get_turn_by_id(turn_id)
    if not turn:
        raise HTTPException(status_code=404, detail="Turn not found")
    return {"turn_id": turn_id, "question": turn.get("question"), "answer": turn.get("answer")}
