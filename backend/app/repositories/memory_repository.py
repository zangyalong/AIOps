from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory


async def list_active(
    db: AsyncSession,
    scope_id: str,
    *,
    limit: int = 200,
) -> list[Memory]:
    result = await db.scalars(
        select(Memory)
        .where(Memory.scope_id == scope_id, Memory.is_active.is_(True))
        .order_by(Memory.updated_at.desc(), Memory.id)
        .limit(limit)
    )
    return list(result)


async def list_all(db: AsyncSession, scope_id: str) -> list[Memory]:
    result = await db.scalars(
        select(Memory)
        .where(Memory.scope_id == scope_id)
        .order_by(Memory.is_active.desc(), Memory.updated_at.desc(), Memory.id)
    )
    return list(result)


async def get_by_id(db: AsyncSession, memory_id: uuid.UUID) -> Memory | None:
    return await db.get(Memory, memory_id)


async def upsert(
    db: AsyncSession,
    *,
    scope_id: str,
    memory_key: str,
    category: str,
    content: str,
    source_conversation_id: uuid.UUID,
    source_run_id: uuid.UUID,
) -> Memory:
    statement = (
        insert(Memory)
        .values(
            id=uuid.uuid4(),
            scope_id=scope_id,
            memory_key=memory_key,
            category=category,
            content=content,
            source_conversation_id=source_conversation_id,
            source_run_id=source_run_id,
            is_active=True,
        )
        .on_conflict_do_update(
            constraint="uq_memories_scope_key",
            set_={
                "category": category,
                "content": content,
                "source_conversation_id": source_conversation_id,
                "source_run_id": source_run_id,
                "is_active": True,
                "updated_at": func.now(),
            },
        )
        .returning(Memory)
    )
    result = await db.scalar(statement)
    assert result is not None
    return result


async def deactivate_by_key(
    db: AsyncSession,
    *,
    scope_id: str,
    memory_key: str,
) -> int:
    result = await db.execute(
        update(Memory)
        .where(
            Memory.scope_id == scope_id,
            Memory.memory_key == memory_key,
            Memory.is_active.is_(True),
        )
        .values(is_active=False, updated_at=func.now())
    )
    return int(result.rowcount or 0)


async def deactivate_one(
    db: AsyncSession,
    *,
    scope_id: str,
    memory_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        update(Memory)
        .where(
            Memory.id == memory_id,
            Memory.scope_id == scope_id,
            Memory.is_active.is_(True),
        )
        .values(is_active=False, updated_at=func.now())
    )
    return bool(result.rowcount)


async def deactivate_many(
    db: AsyncSession,
    *,
    scope_id: str,
    memory_ids: Sequence[uuid.UUID] | None = None,
) -> int:
    statement = update(Memory).where(
        Memory.scope_id == scope_id,
        Memory.is_active.is_(True),
    )
    if memory_ids is not None:
        statement = statement.where(Memory.id.in_(memory_ids))
    result = await db.execute(statement.values(is_active=False, updated_at=func.now()))
    return int(result.rowcount or 0)
