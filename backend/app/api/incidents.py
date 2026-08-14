"""事件 API（方向二·任务②）：查事件、认领、（人工）恢复。

事件的自动诊断由 worker 回填，自动恢复由 ingestion 处理（告警全 resolved 时）；这里是人要干预的入口。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.dependencies import DBSession
from app.models.alert_occurrence import AlertOccurrence
from app.models.incident import Incident
from app.repositories import alert_occurrence_repository
from app.schemas.alert import AlertOccurrenceRead
from app.schemas.incident import (
    AckRequest,
    IncidentPageRead,
    IncidentRead,
    IncidentStatus,
)
from app.services import incident_service

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=IncidentPageRead)
async def list_incidents(
    db: DBSession,
    status_filter: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
) -> IncidentPageRead:
    """按状态分页列出事件，默认按最近更新倒序。"""
    return await incident_service.list_incidents(
        db,
        status=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{incident_id}/alert-occurrences",
    response_model=list[AlertOccurrenceRead],
)
async def list_incident_alert_occurrences(
    incident_id: uuid.UUID, db: DBSession
) -> list[AlertOccurrence]:
    """列出事件归并到的原始告警触发周期，包括已经恢复的历史周期。"""
    incident = await incident_service.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return await alert_occurrence_repository.list_by_incident(db, incident_id)


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


@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_incident(incident_id: uuid.UUID, db: DBSession) -> Response:
    """删除一条已解决的历史事件；活动事件必须先解决。"""
    try:
        deleted = await incident_service.delete_resolved(db, incident_id)
    except incident_service.IncidentDeleteConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="incident not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
