from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent
from app.models.alert_occurrence import AlertOccurrence


async def upsert_for_incident(
    db: AsyncSession,
    *,
    alert: AlertEvent,
    incident_id: uuid.UUID,
    status: str,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> AlertOccurrence:
    occurrence = await db.scalar(
        select(AlertOccurrence).where(
            AlertOccurrence.fingerprint == alert.fingerprint,
            AlertOccurrence.incident_id == incident_id,
        )
    )
    if occurrence is None:
        occurrence = AlertOccurrence(
            alert_event_id=alert.id,
            incident_id=incident_id,
            fingerprint=alert.fingerprint,
            alertname=alert.alertname,
            severity=alert.severity,
            status=status,
            summary=alert.summary,
            labels=alert.labels,
            instance=alert.instance,
            starts_at=starts_at if starts_at is not None else alert.starts_at,
            ends_at=ends_at,
        )
        db.add(occurrence)
        return occurrence

    occurrence.alert_event_id = alert.id
    occurrence.alertname = alert.alertname
    occurrence.severity = alert.severity
    occurrence.status = status
    occurrence.summary = alert.summary
    occurrence.labels = alert.labels
    occurrence.instance = alert.instance
    if starts_at is not None:
        occurrence.starts_at = starts_at
    elif occurrence.starts_at is None:
        occurrence.starts_at = alert.starts_at
    occurrence.ends_at = ends_at
    return occurrence


async def list_by_incident(
    db: AsyncSession, incident_id: uuid.UUID
) -> list[AlertOccurrence]:
    result = await db.scalars(
        select(AlertOccurrence)
        .where(AlertOccurrence.incident_id == incident_id)
        .order_by(AlertOccurrence.created_at)
    )
    return list(result)
