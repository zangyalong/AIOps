from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_run import ChatRun


async def create(db: AsyncSession, chat_run: ChatRun) -> ChatRun:
    db.add(chat_run)
    await db.commit()
    await db.refresh(chat_run)
    return chat_run


async def get_by_id(db: AsyncSession, run_id: uuid.UUID) -> ChatRun | None:
    return await db.get(ChatRun, run_id)


async def list_by_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> list[ChatRun]:
    result = await db.scalars(
        select(ChatRun)
        .where(ChatRun.conversation_id == conversation_id)
        .order_by(ChatRun.started_at, ChatRun.id)
    )
    return list(result)


async def save(db: AsyncSession, chat_run: ChatRun) -> ChatRun:
    await db.commit()
    await db.refresh(chat_run)
    return chat_run


async def fail_incomplete(
    db: AsyncSession, *, error_message: str, finished_at: datetime
) -> int:
    result = await db.execute(
        update(ChatRun)
        .where(ChatRun.status.in_(("running", "waiting_approval")))
        .values(
            status="failed",
            error_message=error_message,
            finished_at=finished_at,
        )
    )
    await db.commit()
    return int(result.rowcount or 0)
