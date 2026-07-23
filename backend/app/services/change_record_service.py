from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_record import ChangeRecord
from app.models.incident import Incident
from app.repositories import change_record_repository
from app.schemas.change_record import ChangeRecordCreate, ChangeRecordUpdate
from app.services import server_service, service_catalog_service


@dataclass(frozen=True)
class IncidentChangeContext:
    service_id: uuid.UUID | None
    service_name: str | None
    environment: str | None
    window_start: datetime
    window_end: datetime
    changes: list[ChangeRecord]


async def list_change_records(
    db: AsyncSession,
    *,
    service_name: str | None = None,
    environment: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 100,
) -> list[ChangeRecord]:
    return await change_record_repository.list_recent(
        db,
        service_name=service_name,
        environment=environment,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )


async def get_change_record(
    db: AsyncSession, record_id: uuid.UUID
) -> ChangeRecord | None:
    return await change_record_repository.get(db, record_id)


async def create_change_record(
    db: AsyncSession, data: ChangeRecordCreate
) -> ChangeRecord:
    return await change_record_repository.create(db, ChangeRecord(**data.model_dump()))


async def update_change_record(
    db: AsyncSession, record: ChangeRecord, data: ChangeRecordUpdate
) -> ChangeRecord:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(record, field, value)
    return await change_record_repository.save(db, record)


async def delete_change_record(db: AsyncSession, record: ChangeRecord) -> None:
    await change_record_repository.delete(db, record)


async def get_incident_change_context(
    db: AsyncSession, incident: Incident, *, window_minutes: int = 30
) -> IncidentChangeContext:
    """按事件实例映射到服务，再查事件发生前后同一时间窗内的变更。"""
    anchor = incident.created_at
    window_start = anchor - timedelta(minutes=window_minutes)
    window_end = anchor + timedelta(minutes=window_minutes)

    resolved = await service_catalog_service.resolve_by_metric_instance(
        db, incident.instance
    )
    service_id: uuid.UUID | None = None
    service_name: str | None = None
    environment: str | None = None
    if resolved is not None:
        service_id = resolved.service.id
        service_name = resolved.service.name
        environment = resolved.service.environment
    else:
        # 兼容服务目录落地前的旧数据；新实例应优先登记到 service_instances。
        servers = await server_service.list_servers(db)
        server = next(
            (
                item
                for item in servers
                if (item.monitor_labels or {}).get("instance") == incident.instance
            ),
            None,
        )
        labels = (server.monitor_labels or {}) if server is not None else {}
        service_name = labels.get("service")
        environment = labels.get("environment") or labels.get("env")
    changes: list[ChangeRecord] = []
    if service_name:
        changes = await list_change_records(
            db,
            service_name=service_name,
            environment=environment,
            start_at=window_start,
            end_at=window_end,
            limit=100,
        )

    return IncidentChangeContext(
        service_id=service_id,
        service_name=service_name,
        environment=environment,
        window_start=window_start,
        window_end=window_end,
        changes=changes,
    )
