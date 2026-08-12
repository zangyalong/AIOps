from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operation_record import OperationRecord
from app.operations.schemas import ApprovalDecision, PreparedOperation
from app.repositories import operation_record_repository

APPROVAL_TTL = timedelta(minutes=15)
MAX_RESULT_CHARS = 16_000


class OperationRecordError(RuntimeError):
    pass


class OperationRequestMismatch(OperationRecordError):
    pass


class OperationApprovalExpired(OperationRecordError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_binding(record: OperationRecord, prepared: PreparedOperation) -> None:
    current_hash = prepared.call.request_hash(prepared.spec, prepared.context)
    if (
        record.operation_name != prepared.call.name
        or record.request_hash != current_hash
    ):
        raise OperationRequestMismatch(
            "待执行操作与原审批请求不一致，已拒绝执行；请重新发起操作。"
        )


async def ensure_pending(
    db: AsyncSession,
    *,
    execution_id: uuid.UUID,
    chat_run_id: uuid.UUID,
    prepared: PreparedOperation,
) -> OperationRecord:
    """幂等创建待审批记录；同一 tool_call_id 不允许换参数。"""
    existing = await operation_record_repository.get_by_execution_and_tool_call(
        db,
        execution_id,
        prepared.call.id,
    )
    if existing is not None:
        _validate_binding(existing, prepared)
        return existing

    if prepared.spec.effect != "write" or prepared.approval_view is None:
        raise OperationRecordError("只有待审批写操作可以创建操作记录")
    now = _now()
    return await operation_record_repository.create(
        db,
        OperationRecord(
            execution_id=execution_id,
            chat_run_id=chat_run_id,
            tool_call_id=prepared.call.id,
            operation_name=prepared.call.name,
            effect=prepared.spec.effect,
            risk_level=prepared.spec.risk_level,
            normalized_args=prepared.call.normalized_args(),
            request_hash=prepared.call.request_hash(prepared.spec, prepared.context),
            approval_view=dict(prepared.approval_view),
            actor_id=prepared.context.actor_id,
            actor_role=prepared.context.actor_role,
            run_mode=prepared.context.run_mode,
            environment=prepared.context.environment,
            policy_reason=prepared.decision.reason or None,
            status="pending_approval",
            expires_at=now + APPROVAL_TTL,
        ),
    )


async def begin_read_execution(
    db: AsyncSession,
    *,
    execution_id: uuid.UUID,
    chat_run_id: uuid.UUID | None,
    prepared: PreparedOperation,
) -> OperationRecord:
    """在真实执行前落下低风险只读审计；审计不可用时调用方必须停止执行。"""
    if prepared.spec.effect != "read" or not prepared.spec.audit_execution:
        raise OperationRecordError("该读操作没有登记为强制审计操作")
    if prepared.decision.result != "allow":
        raise OperationRecordError("只有策略允许的只读操作才能开始执行")

    existing = await operation_record_repository.get_by_execution_and_tool_call(
        db,
        execution_id,
        prepared.call.id,
    )
    if existing is not None:
        _validate_binding(existing, prepared)
        if existing.status != "executing":
            raise OperationRecordError(
                f"只读操作当前状态为 {existing.status}，不能重复执行。"
            )
        return existing

    now = _now()
    return await operation_record_repository.create(
        db,
        OperationRecord(
            execution_id=execution_id,
            chat_run_id=chat_run_id,
            tool_call_id=prepared.call.id,
            operation_name=prepared.call.name,
            effect="read",
            risk_level=prepared.spec.risk_level,
            normalized_args=prepared.call.normalized_args(),
            request_hash=prepared.call.request_hash(prepared.spec, prepared.context),
            approval_view={},
            actor_id=prepared.context.actor_id,
            actor_role=prepared.context.actor_role,
            run_mode=prepared.context.run_mode,
            environment=prepared.context.environment,
            policy_reason=prepared.decision.reason or None,
            status="executing",
            decision_at=now,
            started_at=now,
            expires_at=now,
        ),
    )


async def authorize_execution(
    db: AsyncSession,
    *,
    execution_id: uuid.UUID,
    prepared: PreparedOperation,
    approval: ApprovalDecision | None,
) -> OperationRecord:
    """校验审批绑定并原子推进为 executing；非 allow 决议落为 denied。"""
    record = await operation_record_repository.get_by_execution_and_tool_call(
        db,
        execution_id,
        prepared.call.id,
        for_update=True,
    )
    if record is None:
        raise OperationRecordError("没有找到该操作的原始审批记录，已拒绝执行。")
    _validate_binding(record, prepared)
    if approval is not None and (
        (approval.operation_id and approval.operation_id != str(record.id))
        or (approval.tool_call_id and approval.tool_call_id != record.tool_call_id)
    ):
        raise OperationRequestMismatch("审批决议与当前冻结操作不匹配，已拒绝执行。")
    if record.status != "pending_approval":
        raise OperationRecordError(
            f"操作当前状态为 {record.status}，不能重复审批或执行。"
        )

    now = _now()
    if approval is not None:
        record.approved = approval.approved
        record.second_confirmation = approval.second_confirmation
        record.approval_reason = approval.reason
        record.approver_id = approval.approver_id
        record.approver_role = approval.approver_role
    record.policy_reason = prepared.decision.reason or None
    record.decision_at = now
    if record.expires_at <= now:
        record.status = "expired"
        record.finished_at = now
        await operation_record_repository.save(db, record)
        raise OperationApprovalExpired("审批请求已过期，未执行；请重新发起操作。")

    if prepared.decision.result != "allow":
        record.status = "denied"
        record.finished_at = now
        record.error_message = prepared.decision.reason
        return await operation_record_repository.save(db, record)

    record.status = "executing"
    record.started_at = now
    return await operation_record_repository.save(db, record)


async def finish_execution(
    db: AsyncSession,
    *,
    execution_id: uuid.UUID,
    tool_call_id: str,
    result: object | None = None,
    error: str | None = None,
    metadata: dict[str, object] | None = None,
) -> OperationRecord:
    record = await operation_record_repository.get_by_execution_and_tool_call(
        db,
        execution_id,
        tool_call_id,
    )
    if record is None:
        raise OperationRecordError("没有找到正在执行的操作记录")
    if record.status != "executing":
        raise OperationRecordError(f"操作状态 {record.status} 不能结束执行")
    record.finished_at = _now()
    if metadata is not None:
        raw_exit_code = metadata.get("exit_code")
        raw_timed_out = metadata.get("timed_out")
        if isinstance(raw_exit_code, int) and not isinstance(raw_exit_code, bool):
            record.exit_code = raw_exit_code
        if isinstance(raw_timed_out, bool):
            record.timed_out = raw_timed_out
    if error is not None:
        record.status = "failed"
        record.error_message = error[:MAX_RESULT_CHARS]
    else:
        record.status = "completed"
        record.result = str(result)[:MAX_RESULT_CHARS]
    return await operation_record_repository.save(db, record)


async def list_operation_records(
    db: AsyncSession,
    *,
    execution_id: uuid.UUID | None = None,
    chat_run_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[OperationRecord]:
    return await operation_record_repository.list_records(
        db,
        execution_id=execution_id,
        chat_run_id=chat_run_id,
        status=status,
        limit=limit,
    )


async def list_conversation_operation_records(
    db: AsyncSession, conversation_id: uuid.UUID
) -> list[OperationRecord]:
    return await operation_record_repository.list_by_conversation(db, conversation_id)


async def fail_incomplete_operations(db: AsyncSession) -> int:
    return await operation_record_repository.fail_incomplete(db, now=_now())
