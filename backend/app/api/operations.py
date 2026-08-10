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
    """按时间倒序查询 root 只读调查及写操作审批、执行审计。"""
    return await operation_record_service.list_operation_records(
        db,
        chat_run_id=chat_run_id,
        status=status,
        limit=limit,
    )
