from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent
from app.models.incident import RESOLVED, Incident


async def get_active_by_group_key(db: AsyncSession, group_key: str) -> Incident | None:
    """找某 group_key 当前「活跃」（未 resolved）的事件；没有返回 None。

    任一时刻每个 group_key 最多一个活跃事件——新告警 firing 时，有活跃事件就挂上去，没有就新建。
    """
    return await db.scalar(
        select(Incident).where(
            Incident.group_key == group_key, Incident.status != RESOLVED
        )
    )


async def create(
    db: AsyncSession,
    *,
    group_key: str,
    alertname: str,
    severity: str,
    instance: str | None,
    summary: str | None,
) -> Incident:
    incident = Incident(
        group_key=group_key,
        alertname=alertname,
        severity=severity,
        instance=instance,
        summary=summary,
    )
    db.add(incident)
    return incident


async def get(db: AsyncSession, incident_id: uuid.UUID) -> Incident | None:
    return await db.get(Incident, incident_id)


async def list_recent(
    db: AsyncSession, status: str | None = None, limit: int = 100
) -> list[Incident]:
    stmt = select(Incident).order_by(Incident.updated_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Incident.status == status)
    result = await db.scalars(stmt)
    return list(result)


async def list_page(
    db: AsyncSession,
    *,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Incident], int]:
    """按最近更新时间倒序读取一页，并返回同一筛选条件下的总数。"""
    filters = (Incident.status == status,) if status is not None else ()
    total = await db.scalar(select(func.count()).select_from(Incident).where(*filters))
    result = await db.scalars(
        select(Incident)
        .where(*filters)
        .order_by(Incident.updated_at.desc(), Incident.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result), total or 0


async def count_by_status(db: AsyncSession) -> dict[str, int]:
    """一次聚合查询得到各状态的全库数量。"""
    rows = await db.execute(
        select(Incident.status, func.count()).group_by(Incident.status)
    )
    return {str(status): int(count) for status, count in rows.all()}


async def delete(db: AsyncSession, incident: Incident) -> None:
    await db.delete(incident)
    await db.commit()


async def count_firing_alerts(db: AsyncSession, incident_id: uuid.UUID) -> int:
    """事件下当前还有几条 firing 的告警——全 0 了就可以自动把事件置为 resolved。"""
    stmt = (
        select(func.count())
        .select_from(AlertEvent)
        .where(AlertEvent.incident_id == incident_id, AlertEvent.status == "firing")
    )
    return await db.scalar(stmt) or 0
