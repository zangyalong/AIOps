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
    group_key: str | None,
) -> tuple[AlertEvent, bool]:
    """按 fingerprint upsert，返回 (告警, 是否「新触发」)。

    「新触发」= 新插入的 firing，或原本非 firing 现在变 firing——用来决定要不要触发自动诊断
    （重复推送的同一条 firing、或 resolved，都不算，避免反复诊断）。
    不在这里 commit——由 service 批量处理完一次性 commit（一个 webhook 可能带多条告警）。
    """
    existing = await db.scalar(
        select(AlertEvent).where(AlertEvent.fingerprint == fingerprint)
    )
    if existing is not None:
        was_firing = existing.status == "firing"
        existing.alertname = alertname
        existing.severity = severity
        existing.status = status
        existing.summary = summary
        existing.labels = labels
        existing.instance = instance
        existing.starts_at = starts_at
        existing.ends_at = ends_at
        existing.group_key = group_key
        newly_firing = (not was_firing) and status == "firing"
        return existing, newly_firing

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
        group_key=group_key,
    )
    db.add(alert)
    return alert, status == "firing"


async def get_by_fingerprint(db: AsyncSession, fingerprint: str) -> AlertEvent | None:
    return await db.scalar(
        select(AlertEvent).where(AlertEvent.fingerprint == fingerprint)
    )


async def list_recent(
    db: AsyncSession, limit: int = 100, status: str | None = None
) -> list[AlertEvent]:
    stmt = select(AlertEvent).order_by(AlertEvent.updated_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(AlertEvent.status == status)
    result = await db.scalars(stmt)
    return list(result)
