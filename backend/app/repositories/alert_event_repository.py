from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent


async def create(db: AsyncSession, alert: AlertEvent) -> AlertEvent:
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


async def list_open(db: AsyncSession, server_id: uuid.UUID | None = None) -> list[AlertEvent]:
    stmt = (
        select(AlertEvent)
        .where(AlertEvent.status == "open")
        .order_by(AlertEvent.created_at.desc())
    )
    if server_id is not None:
        stmt = stmt.where(AlertEvent.server_id == server_id)
    result = await db.scalars(stmt)
    return list(result)


async def exists(db: AsyncSession, server_id: uuid.UUID | None, severity: str, message: str) -> bool:
    """按 (server_id, severity, message) 判断这条告警是不是已经种过了，给种子脚本去重用。"""
    stmt = select(AlertEvent.id).where(
        AlertEvent.server_id == server_id,
        AlertEvent.severity == severity,
        AlertEvent.message == message,
    )
    result = await db.scalars(stmt)
    return result.first() is not None
