from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import DBSession
from app.models.change_record import ChangeRecord
from app.schemas.change_record import (
    ChangeRecordCreate,
    ChangeRecordRead,
    ChangeRecordUpdate,
)
from app.services import change_record_service

router = APIRouter(prefix="/changes", tags=["changes"])


async def _get_record_or_404(db: AsyncSession, record_id: uuid.UUID) -> ChangeRecord:
    record = await change_record_service.get_change_record(db, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="change record not found")
    return record


@router.get("", response_model=list[ChangeRecordRead])
async def list_change_records(
    db: DBSession,
    service_name: str | None = None,
    environment: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ChangeRecord]:
    return await change_record_service.list_change_records(
        db,
        service_name=service_name,
        environment=environment,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )


@router.post("", response_model=ChangeRecordRead, status_code=201)
async def create_change_record(data: ChangeRecordCreate, db: DBSession) -> ChangeRecord:
    return await change_record_service.create_change_record(db, data)


@router.get("/{record_id}", response_model=ChangeRecordRead)
async def get_change_record(record_id: uuid.UUID, db: DBSession) -> ChangeRecord:
    return await _get_record_or_404(db, record_id)


@router.patch("/{record_id}", response_model=ChangeRecordRead)
async def update_change_record(
    record_id: uuid.UUID, data: ChangeRecordUpdate, db: DBSession
) -> ChangeRecord:
    record = await _get_record_or_404(db, record_id)
    return await change_record_service.update_change_record(db, record, data)


@router.delete("/{record_id}", status_code=204)
async def delete_change_record(record_id: uuid.UUID, db: DBSession) -> None:
    record = await _get_record_or_404(db, record_id)
    await change_record_service.delete_change_record(db, record)
