from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import ACKNOWLEDGED, RESOLVED, Incident
from app.repositories import incident_repository


async def list_incidents(
    db: AsyncSession, status: str | None = None, limit: int = 100
) -> list[Incident]:
    return await incident_repository.list_recent(db, status=status, limit=limit)


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
