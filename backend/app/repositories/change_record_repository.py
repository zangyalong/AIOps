from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_record import ChangeRecord


async def list_recent(
    db: AsyncSession,
    *,
    service_name: str | None = None,
    environment: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 100,
) -> list[ChangeRecord]:
    stmt = select(ChangeRecord)
    if service_name is not None:
        stmt = stmt.where(ChangeRecord.service_name == service_name)
    if environment is not None:
        stmt = stmt.where(ChangeRecord.environment == environment)
    if start_at is not None:
        stmt = stmt.where(ChangeRecord.occurred_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(ChangeRecord.occurred_at <= end_at)
    stmt = stmt.order_by(ChangeRecord.occurred_at.desc()).limit(limit)
    result = await db.scalars(stmt)
    return list(result)


async def get(db: AsyncSession, record_id: uuid.UUID) -> ChangeRecord | None:
    return await db.get(ChangeRecord, record_id)


async def create(db: AsyncSession, record: ChangeRecord) -> ChangeRecord:
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def save(db: AsyncSession, record: ChangeRecord) -> ChangeRecord:
    await db.commit()
    await db.refresh(record)
    return record


async def delete(db: AsyncSession, record: ChangeRecord) -> None:
    await db.delete(record)
    await db.commit()
