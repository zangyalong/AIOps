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
    steps: list[dict] | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        steps=steps,
    )
    return await message_repository.create(db, message)


async def list_messages(db: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    return await message_repository.list_by_conversation(db, conversation_id)
