"""事件 API（方向二·任务②）：查事件、认领、（人工）恢复。

事件的自动诊断由 worker 回填，自动恢复由 ingestion 处理（告警全 resolved 时）；这里是人要干预的入口。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.dependencies import DBSession
from app.models.incident import Incident
from app.schemas.incident import AckRequest, IncidentRead
from app.services import incident_service

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=list[IncidentRead])
async def list_incidents(
    db: DBSession, status: str | None = None, limit: int = 100
) -> list[Incident]:
    """列出事件，可按 status（firing/acknowledged/resolved）过滤，默认按最近更新倒序。"""
    return await incident_service.list_incidents(db, status=status, limit=limit)


@router.post("/{incident_id}/ack", response_model=IncidentRead)
async def ack_incident(
    incident_id: uuid.UUID, payload: AckRequest, db: DBSession
) -> Incident:
    """认领事件：置为 acknowledged，记下谁认领。"""
    incident = await incident_service.acknowledge(db, incident_id, payload.acked_by)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return incident


@router.post("/{incident_id}/resolve", response_model=IncidentRead)
async def resolve_incident(incident_id: uuid.UUID, db: DBSession) -> Incident:
    """人工强制恢复事件（正常情况下告警全恢复会自动 resolve）。"""
    incident = await incident_service.resolve(db, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return incident
