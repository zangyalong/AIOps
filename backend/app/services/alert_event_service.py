from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent
from app.repositories import alert_event_repository


async def create_alert(
    db: AsyncSession, server_id: uuid.UUID | None, severity: str, message: str
) -> AlertEvent:
    alert = AlertEvent(server_id=server_id, severity=severity, message=message)
    return await alert_event_repository.create(db, alert)


async def list_open_alerts(db: AsyncSession, server_id: uuid.UUID | None = None) -> list[AlertEvent]:
    return await alert_event_repository.list_open(db, server_id)


async def alert_exists(
    db: AsyncSession, server_id: uuid.UUID | None, severity: str, message: str
) -> bool:
    return await alert_event_repository.exists(db, server_id, severity, message)
