from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation


async def create(db: AsyncSession, conversation: Conversation) -> Conversation:
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def list_all(db: AsyncSession) -> list[Conversation]:
    result = await db.scalars(
        select(Conversation).order_by(Conversation.updated_at.desc())
    )
    return list(result)


async def get_by_id(db: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    return await db.get(Conversation, conversation_id)


async def touch(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """把 updated_at 刷新成当前时间，让活跃对话排到列表前面。"""
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=func.now())
    )
    await db.commit()
