from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent
from app.models.alert_occurrence import AlertOccurrence
from app.models.incident import Incident
from app.repositories import alert_occurrence_repository


async def backfill_existing_incidents(db: AsyncSession) -> int:
    """为引入历史表之前的事件补一条告警快照；可重复执行。"""
    incidents = list(await db.scalars(select(Incident).order_by(Incident.created_at)))
    inserted = 0
    for incident in incidents:
        alerts = list(
            await db.scalars(
                select(AlertEvent).where(AlertEvent.group_key == incident.group_key)
            )
        )
        for alert in alerts:
            existing = await db.scalar(
                select(AlertOccurrence.id).where(
                    AlertOccurrence.fingerprint == alert.fingerprint,
                    AlertOccurrence.incident_id == incident.id,
                )
            )
            if existing is not None:
                continue
            await alert_occurrence_repository.upsert_for_incident(
                db,
                alert=alert,
                incident_id=incident.id,
                status=incident.status,
                starts_at=incident.created_at,
                ends_at=incident.resolved_at,
            )
            await db.flush()
            inserted += 1
    if inserted:
        await db.commit()
    return inserted
