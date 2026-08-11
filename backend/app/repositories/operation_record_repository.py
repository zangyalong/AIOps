from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operation_record import OperationRecord


async def create(db: AsyncSession, record: OperationRecord) -> OperationRecord:
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_by_execution_and_tool_call(
    db: AsyncSession,
    execution_id: uuid.UUID,
    tool_call_id: str,
    *,
    for_update: bool = False,
) -> OperationRecord | None:
    statement = select(OperationRecord).where(
        OperationRecord.execution_id == execution_id,
        OperationRecord.tool_call_id == tool_call_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return await db.scalar(statement)


async def save(db: AsyncSession, record: OperationRecord) -> OperationRecord:
    await db.commit()
    await db.refresh(record)
    return record


async def list_records(
    db: AsyncSession,
    *,
    execution_id: uuid.UUID | None = None,
    chat_run_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[OperationRecord]:
    statement = select(OperationRecord)
    if execution_id is not None:
        statement = statement.where(OperationRecord.execution_id == execution_id)
    if chat_run_id is not None:
        statement = statement.where(OperationRecord.chat_run_id == chat_run_id)
    if status is not None:
        statement = statement.where(OperationRecord.status == status)
    result = await db.scalars(
        statement.order_by(OperationRecord.created_at.desc()).limit(limit)
    )
    return list(result)


async def fail_incomplete(db: AsyncSession, *, now: datetime) -> int:
    result = await db.execute(
        update(OperationRecord)
        .where(OperationRecord.status == "executing")
        .values(
            status="failed",
            finished_at=now,
            error_message="后端进程重启，操作执行结果未知，请人工复核目标状态。",
        )
    )
    await db.execute(
        update(OperationRecord)
        .where(
            OperationRecord.status == "pending_approval",
            OperationRecord.expires_at <= now,
        )
        .values(status="expired", finished_at=now)
    )
    await db.commit()
    return int(result.rowcount or 0)
