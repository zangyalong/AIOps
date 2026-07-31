from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message


async def create(db: AsyncSession, message: Message) -> Message:
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def list_by_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> list[Message]:
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(result)


async def list_by_run(db: AsyncSession, run_id: uuid.UUID) -> list[Message]:
    result = await db.scalars(
        select(Message)
        .where(Message.run_id == run_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(result)
