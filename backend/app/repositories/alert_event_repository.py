from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent


async def upsert_by_fingerprint(
    db: AsyncSession,
    *,
    fingerprint: str,
    alertname: str,
    severity: str,
    status: str,
    summary: str | None,
    labels: dict | None,
    instance: str | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> AlertEvent:
    """按 fingerprint upsert：已存在就更新（同一条告警反复 firing/resolved 更新同一行），否则插入。

    不在这里 commit——由 service 批量处理完一次性 commit（一个 webhook 可能带多条告警）。
    """
    existing = await db.scalar(select(AlertEvent).where(AlertEvent.fingerprint == fingerprint))
    if existing is not None:
        existing.alertname = alertname
        existing.severity = severity
        existing.status = status
        existing.summary = summary
        existing.labels = labels
        existing.instance = instance
        existing.starts_at = starts_at
        existing.ends_at = ends_at
        return existing

    alert = AlertEvent(
        fingerprint=fingerprint,
        alertname=alertname,
        severity=severity,
        status=status,
        summary=summary,
        labels=labels,
        instance=instance,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    db.add(alert)
    return alert


async def list_recent(
    db: AsyncSession, limit: int = 100, status: str | None = None
) -> list[AlertEvent]:
    stmt = select(AlertEvent).order_by(AlertEvent.updated_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(AlertEvent.status == status)
    result = await db.scalars(stmt)
    return list(result)
