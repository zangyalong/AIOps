from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.repositories import message_repository


async def create_message(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    run_id: uuid.UUID | None = None,
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        run_id=run_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )
    return await message_repository.create(db, message)


async def list_messages(db: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    return await message_repository.list_by_conversation(db, conversation_id)


async def list_run_messages(db: AsyncSession, run_id: uuid.UUID) -> list[Message]:
    return await message_repository.list_by_run(db, run_id)
