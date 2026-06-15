"""Conversation persistence API — save/load/delete user conversations in MongoDB.

Endpoints:
    GET    /conversations          → list user's conversations
    GET    /conversations/{id}     → get a conversation with messages
    POST   /conversations          → save/update a conversation
    DELETE /conversations/{id}     → delete a conversation
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from api.auth import get_current_user
from db.user_data import (
    save_conversation,
    get_user_conversations,
    get_conversation,
    delete_conversation,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


class SaveConversationRequest(BaseModel):
    conv_id: str
    title: str
    messages: List[Dict[str, Any]]


@router.get("")
def list_conversations(current_user: dict = Depends(get_current_user)):
    """List all conversations for the current user."""
    convs = get_user_conversations(current_user["user_id"])
    # Convert datetime objects to ISO strings for JSON serialization
    for c in convs:
        for key in ("updated_at", "created_at"):
            if key in c and c[key]:
                c[key] = c[key].isoformat() if hasattr(c[key], "isoformat") else str(c[key])
    return convs


@router.get("/{conv_id}")
def get_conv(conv_id: str, current_user: dict = Depends(get_current_user)):
    """Get a full conversation including messages."""
    conv = get_conversation(current_user["user_id"], conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    for key in ("updated_at", "created_at"):
        if key in conv and conv[key]:
            conv[key] = conv[key].isoformat() if hasattr(conv[key], "isoformat") else str(conv[key])
    return conv


@router.post("")
def save_conv(req: SaveConversationRequest, current_user: dict = Depends(get_current_user)):
    """Save or update a conversation."""
    save_conversation(
        user_id=current_user["user_id"],
        conv_id=req.conv_id,
        title=req.title,
        messages=req.messages,
    )
    return {"ok": True}


@router.delete("/{conv_id}")
def delete_conv(conv_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a conversation."""
    deleted = delete_conversation(current_user["user_id"], conv_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}
