from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.dependencies import DBSession
from app.schemas.chat import ChatMessageOut, ConversationDetail, ConversationSummary
from app.services import conversation_service, message_service

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(db: DBSession) -> list[ConversationSummary]:
    conversations = await conversation_service.list_conversations(db)
    return [ConversationSummary.model_validate(c) for c in conversations]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, db: DBSession
) -> ConversationDetail:
    conversation = await conversation_service.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    messages = await message_service.list_messages(db, conversation_id)

    return ConversationDetail(
        conversation=ConversationSummary.model_validate(conversation),
        messages=[ChatMessageOut(role=m.role, content=m.content) for m in messages],
    )
