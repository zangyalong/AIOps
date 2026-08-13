from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.dependencies import DBSession
from app.schemas.chat import (
    ChatMessageOut,
    ChatRunHistoryOut,
    ConversationDetail,
    ConversationSummary,
)
from app.services import (
    chat_run_record_service,
    conversation_service,
    message_service,
    operation_record_service,
)

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
    chat_runs = await chat_run_record_service.list_chat_runs(db, conversation_id)
    operation_records = (
        await operation_record_service.list_conversation_operation_records(
            db, conversation_id
        )
    )
    operation_status = {
        (record.chat_run_id, record.tool_call_id): record.status
        for record in operation_records
    }
    operation_approval_args = {
        (record.chat_run_id, record.tool_call_id): record.approval_view.get("args")
        for record in operation_records
        if isinstance(record.approval_view, dict)
        and isinstance(record.approval_view.get("args"), dict)
    }

    return ConversationDetail(
        conversation=ConversationSummary.model_validate(conversation),
        runs=[ChatRunHistoryOut.model_validate(run) for run in chat_runs],
        messages=[
            ChatMessageOut(
                role=m.role,
                content=m.content,
                run_id=m.run_id,
                tool_calls=m.tool_calls,
                tool_call_id=m.tool_call_id,
                tool_status=(
                    "failed"
                    if operation_status.get((m.run_id, m.tool_call_id))
                    in {"failed", "denied", "expired"}
                    else "completed"
                    if operation_status.get((m.run_id, m.tool_call_id)) == "completed"
                    else None
                ),
                approval_args=operation_approval_args.get((m.run_id, m.tool_call_id)),
            )
            for m in messages
        ],
    )


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: uuid.UUID, db: DBSession) -> None:
    conversation = await conversation_service.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    await conversation_service.delete_conversation(db, conversation)
