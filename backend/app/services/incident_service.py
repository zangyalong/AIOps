from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import ACKNOWLEDGED, RESOLVED, Incident
from app.repositories import incident_repository
from app.schemas.incident import IncidentPageRead, IncidentStatusCounts


class IncidentDeleteConflict(ValueError):
    """活动事件不能直接删除，必须先结束其生命周期。"""


async def list_incidents(
    db: AsyncSession,
    *,
    status: str | None,
    page: int,
    page_size: int,
) -> IncidentPageRead:
    items, total = await incident_repository.list_page(
        db,
        status=status,
        page=page,
        page_size=page_size,
    )
    raw_counts = await incident_repository.count_by_status(db)
    counts = IncidentStatusCounts(
        all=sum(raw_counts.values()),
        firing=raw_counts.get("firing", 0),
        acknowledged=raw_counts.get("acknowledged", 0),
        resolved=raw_counts.get("resolved", 0),
    )
    return IncidentPageRead(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
        status_counts=counts,
    )


async def get_incident(db: AsyncSession, incident_id: uuid.UUID) -> Incident | None:
    return await incident_repository.get(db, incident_id)


async def acknowledge(
    db: AsyncSession, incident_id: uuid.UUID, acked_by: str
) -> Incident | None:
    """认领事件：firing/acknowledged → acknowledged，记谁认领、何时。已 resolved 的不动。"""
    incident = await incident_repository.get(db, incident_id)
    if incident is None:
        return None
    if incident.status != RESOLVED:
        incident.status = ACKNOWLEDGED
        incident.acked_by = acked_by
        incident.acked_at = datetime.now(timezone.utc)
        await db.commit()
        # commit 后 updated_at 等服务端计算列会过期，refresh 在异步上下文里刷新好，
        # 免得后面 API 序列化时访问它触发懒加载（MissingGreenlet）。
        await db.refresh(incident)
    return incident


async def resolve(db: AsyncSession, incident_id: uuid.UUID) -> Incident | None:
    """人工强制恢复事件（自动恢复见 alert_event_service：告警全 resolved 时自动置 resolved）。"""
    incident = await incident_repository.get(db, incident_id)
    if incident is None:
        return None
    if incident.status != RESOLVED:
        incident.status = RESOLVED
        incident.resolved_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(incident)  # 同 acknowledge：刷新好再交给 API 序列化
    return incident


async def delete_resolved(db: AsyncSession, incident_id: uuid.UUID) -> bool:
    """删除已解决事件；关联 occurrence 由数据库级联删除。"""
    incident = await incident_repository.get(db, incident_id)
    if incident is None:
        return False
    if incident.status != RESOLVED:
        raise IncidentDeleteConflict(
            "待处理或已认领事件不能删除，请先将事件标记为已解决"
        )
    await incident_repository.delete(db, incident)
    return True


async def save_diagnosis(
    db: AsyncSession, incident_id: uuid.UUID, diagnosis: str
) -> bool:
    """triage 跑完把诊断回填到事件上。找不到该事件返回 False。"""
    incident = await incident_repository.get(db, incident_id)
    if incident is None:
        return False
    incident.diagnosis = diagnosis
    incident.triaged_at = datetime.now(timezone.utc)
    await db.commit()
    return True
