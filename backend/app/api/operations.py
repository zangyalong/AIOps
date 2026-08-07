from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query

from app.dependencies import DBSession
from app.models.operation_record import OperationRecord
from app.schemas.operation_record import OperationRecordRead
from app.services import operation_record_service

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("", response_model=list[OperationRecordRead])
async def list_operations(
    db: DBSession,
    chat_run_id: uuid.UUID | None = None,
    status: Literal[
        "pending_approval",
        "denied",
        "executing",
        "completed",
        "failed",
        "expired",
    ]
    | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[OperationRecord]:
    """按时间倒序查询写操作审批与执行审计记录。"""
    return await operation_record_service.list_operation_records(
        db,
        chat_run_id=chat_run_id,
        status=status,
        limit=limit,
    )
