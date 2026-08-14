from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory


def _scope_conditions(
    *,
    scope_type: str | None,
    server_id: uuid.UUID | None,
) -> tuple:
    if scope_type is None:
        return ()
    if scope_type == "global":
        return (Memory.scope_type == "global", Memory.server_id.is_(None))
    return (Memory.scope_type == "server", Memory.server_id == server_id)


async def list_active(
    db: AsyncSession,
    scope_id: str,
    *,
    scope_type: str | None = "global",
    server_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[Memory]:
    result = await db.scalars(
        select(Memory)
        .where(
            Memory.scope_id == scope_id,
            Memory.is_active.is_(True),
            *_scope_conditions(scope_type=scope_type, server_id=server_id),
        )
        .order_by(Memory.updated_at.desc(), Memory.id)
        .limit(limit)
    )
    return list(result)


async def list_all(
    db: AsyncSession,
    scope_id: str,
    *,
    scope_type: str | None = "global",
    server_id: uuid.UUID | None = None,
) -> list[Memory]:
    result = await db.scalars(
        select(Memory)
        .where(
            Memory.scope_id == scope_id,
            *_scope_conditions(scope_type=scope_type, server_id=server_id),
        )
        .order_by(Memory.is_active.desc(), Memory.updated_at.desc(), Memory.id)
    )
    return list(result)


async def get_by_id(db: AsyncSession, memory_id: uuid.UUID) -> Memory | None:
    return await db.get(Memory, memory_id)


async def upsert(
    db: AsyncSession,
    *,
    scope_id: str,
    scope_type: str = "global",
    server_id: uuid.UUID | None = None,
    memory_key: str,
    category: str,
    content: str,
    source_conversation_id: uuid.UUID | None,
    source_run_id: uuid.UUID | None,
) -> Memory:
    existing = await db.scalar(
        select(Memory).where(
            Memory.scope_id == scope_id,
            Memory.memory_key == memory_key,
            *_scope_conditions(scope_type=scope_type, server_id=server_id),
        )
    )
    if existing is None:
        existing = Memory(
            scope_id=scope_id,
            scope_type=scope_type,
            server_id=server_id,
            memory_key=memory_key,
            category=category,
            content=content,
            source_conversation_id=source_conversation_id,
            source_run_id=source_run_id,
            is_active=True,
        )
        db.add(existing)
    else:
        existing.category = category
        existing.content = content
        existing.source_conversation_id = source_conversation_id
        existing.source_run_id = source_run_id
        existing.is_active = True
        existing.updated_at = datetime.now(UTC)
    await db.flush()
    return existing


async def deactivate_by_key(
    db: AsyncSession,
    *,
    scope_id: str,
    scope_type: str = "global",
    server_id: uuid.UUID | None = None,
    memory_key: str,
) -> int:
    result = await db.execute(
        update(Memory)
        .where(
            Memory.scope_id == scope_id,
            Memory.memory_key == memory_key,
            Memory.is_active.is_(True),
            *_scope_conditions(scope_type=scope_type, server_id=server_id),
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
    scope_type: str | None = "global",
    server_id: uuid.UUID | None = None,
    memory_ids: Sequence[uuid.UUID] | None = None,
) -> int:
    statement = update(Memory).where(
        Memory.scope_id == scope_id,
        Memory.is_active.is_(True),
        *_scope_conditions(scope_type=scope_type, server_id=server_id),
    )
    if memory_ids is not None:
        statement = statement.where(Memory.id.in_(memory_ids))
    result = await db.execute(statement.values(is_active=False, updated_at=func.now()))
    return int(result.rowcount or 0)
