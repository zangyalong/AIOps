from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

from app.models.operation_record import OperationRecord
from app.operations.schemas import (
    ApprovalDecision,
    OperationCall,
    OperationSpec,
    PolicyDecision,
    PreparedOperation,
)
from app.services import operation_record_service


def _prepared(
    *,
    args: dict | None = None,
    result: str = "allow",
    reason: str = "",
) -> PreparedOperation:
    return PreparedOperation(
        call=OperationCall(
            id="restart-1",
            name="restart_service",
            args=args or {"server_name": "web01", "service_name": "nginx.service"},
        ),
        spec=OperationSpec(
            name="restart_service",
            effect="write",
            risk_level="high",
        ),
        decision=PolicyDecision(result=result, reason=reason),
        approval_view={"title": "重启 nginx.service"},
    )


def _record(
    prepared: PreparedOperation, *, status: str = "pending_approval"
) -> OperationRecord:
    return OperationRecord(
        id=uuid.uuid4(),
        chat_run_id=uuid.uuid4(),
        tool_call_id=prepared.call.id,
        operation_name=prepared.call.name,
        effect="write",
        risk_level=prepared.spec.risk_level,
        normalized_args=prepared.call.normalized_args(),
        request_hash=prepared.call.request_hash(prepared.spec),
        approval_view=dict(prepared.approval_view or {}),
        status=status,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


class OperationCallBindingTest(unittest.TestCase):
    def test_hash_is_stable_for_equivalent_nested_arguments(self) -> None:
        spec = OperationSpec("register", "write", "medium")
        first = OperationCall(
            "call-1",
            "register",
            {"services": [{"name": "api", "port": 8080}], "environment": "prod"},
        )
        second = OperationCall(
            "call-2",
            "register",
            {"environment": "prod", "services": [{"port": 8080, "name": "api"}]},
        )

        self.assertEqual(first.request_hash(spec), second.request_hash(spec))
        self.assertIsInstance(first.args, MappingProxyType)
        with self.assertRaises(TypeError):
            first.args["environment"] = "dev"
        with self.assertRaises(TypeError):
            first.args["services"][0]["name"] = "changed"

    def test_record_contains_only_operation_audit_fields(self) -> None:
        self.assertEqual(
            set(OperationRecord.__table__.columns.keys()),
            {
                "id",
                "chat_run_id",
                "tool_call_id",
                "operation_name",
                "effect",
                "risk_level",
                "normalized_args",
                "request_hash",
                "approval_view",
                "status",
                "approved",
                "second_confirmation",
                "approval_reason",
                "decision_at",
                "started_at",
                "finished_at",
                "result",
                "error_message",
                "expires_at",
                "created_at",
            },
        )


class OperationRecordServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_pending_is_idempotent_and_rejects_changed_request(
        self,
    ) -> None:
        prepared = _prepared()
        record = _record(prepared)
        repository = operation_record_service.operation_record_repository

        with patch.object(
            repository,
            "get_by_run_and_tool_call",
            AsyncMock(return_value=record),
        ):
            result = await operation_record_service.ensure_pending(
                AsyncMock(),
                chat_run_id=record.chat_run_id,
                prepared=prepared,
            )
            self.assertIs(result, record)

            with self.assertRaises(operation_record_service.OperationRequestMismatch):
                await operation_record_service.ensure_pending(
                    AsyncMock(),
                    chat_run_id=record.chat_run_id,
                    prepared=_prepared(args={"server_name": "web02"}),
                )

    async def test_authorize_transitions_only_original_pending_request(self) -> None:
        prepared = _prepared()
        record = _record(prepared)
        save = AsyncMock(side_effect=lambda _db, item: item)
        repository = operation_record_service.operation_record_repository
        with (
            patch.object(
                repository,
                "get_by_run_and_tool_call",
                AsyncMock(return_value=record),
            ),
            patch.object(repository, "save", save),
        ):
            result = await operation_record_service.authorize_execution(
                AsyncMock(),
                chat_run_id=record.chat_run_id,
                prepared=prepared,
                approval=ApprovalDecision(
                    approved=True,
                    second_confirmation=True,
                    reason="值班员确认",
                ),
            )

        self.assertEqual(result.status, "executing")
        self.assertTrue(result.approved)
        self.assertTrue(result.second_confirmation)
        self.assertIsNotNone(result.started_at)

    async def test_denied_and_expired_requests_never_enter_executing(self) -> None:
        denied = _prepared(result="deny", reason="缺少二次确认")
        denied_record = _record(denied)
        repository = operation_record_service.operation_record_repository
        save = AsyncMock(side_effect=lambda _db, item: item)
        with (
            patch.object(
                repository,
                "get_by_run_and_tool_call",
                AsyncMock(return_value=denied_record),
            ),
            patch.object(repository, "save", save),
        ):
            result = await operation_record_service.authorize_execution(
                AsyncMock(),
                chat_run_id=denied_record.chat_run_id,
                prepared=denied,
                approval=ApprovalDecision(approved=True),
            )
        self.assertEqual(result.status, "denied")
        self.assertEqual(result.error_message, "缺少二次确认")

        expired = _record(_prepared())
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with (
            patch.object(
                repository,
                "get_by_run_and_tool_call",
                AsyncMock(return_value=expired),
            ),
            patch.object(repository, "save", save),
            self.assertRaises(operation_record_service.OperationApprovalExpired),
        ):
            await operation_record_service.authorize_execution(
                AsyncMock(),
                chat_run_id=expired.chat_run_id,
                prepared=_prepared(),
                approval=ApprovalDecision(
                    approved=True,
                    second_confirmation=True,
                ),
            )
        self.assertEqual(expired.status, "expired")

    async def test_finish_execution_records_result_or_failure(self) -> None:
        prepared = _prepared()
        completed = _record(prepared, status="executing")
        repository = operation_record_service.operation_record_repository
        save = AsyncMock(side_effect=lambda _db, item: item)
        with (
            patch.object(
                repository,
                "get_by_run_and_tool_call",
                AsyncMock(return_value=completed),
            ),
            patch.object(repository, "save", save),
        ):
            result = await operation_record_service.finish_execution(
                AsyncMock(),
                chat_run_id=completed.chat_run_id,
                tool_call_id=completed.tool_call_id,
                result="已重启",
            )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.result, "已重启")
