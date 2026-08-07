from __future__ import annotations

import unittest
from typing import Any

from app.operations.broker import OperationBroker, OperationNotAllowed
from app.operations.registry import OperationRegistry, RegisteredOperation
from app.operations.schemas import (
    ApprovalDecision,
    OperationCall,
    OperationContext,
    OperationSpec,
)


class OperationBrokerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.executed: list[OperationCall] = []

        async def execute(call: OperationCall) -> Any:
            self.executed.append(call)
            return {"ok": True, "name": call.name}

        self.broker = OperationBroker(
            OperationRegistry(
                [
                    RegisteredOperation(
                        spec=OperationSpec(
                            name="server.query",
                            effect="read",
                            risk_level="low",
                        ),
                        executor=execute,
                    ),
                    RegisteredOperation(
                        spec=OperationSpec(
                            name="service.restart",
                            effect="write",
                            risk_level="high",
                        ),
                        executor=execute,
                        build_approval_view=lambda call: {
                            "tool_call_id": call.id,
                            "title": "重启服务",
                        },
                    ),
                ]
            )
        )

    async def test_prepare_builds_approval_view_for_write_operation(self) -> None:
        prepared = self.broker.prepare(
            OperationCall(
                id="restart-1",
                name="service.restart",
                args={"service": "nginx.service"},
            ),
            OperationContext(write_enabled=True),
        )

        self.assertEqual(prepared.decision.result, "approval_required")
        self.assertEqual(prepared.approval_view["tool_call_id"], "restart-1")
        self.assertEqual(self.executed, [])

    async def test_execute_routes_allowed_read_operation(self) -> None:
        call = OperationCall(id="query-1", name="server.query", args={})
        result = await self.broker.execute(
            call,
            OperationContext(write_enabled=False),
        )

        self.assertEqual(result, {"ok": True, "name": "server.query"})
        self.assertEqual(self.executed, [call])

    async def test_execute_rechecks_policy_before_dispatch(self) -> None:
        call = OperationCall(id="restart-1", name="service.restart", args={})

        with self.assertRaises(OperationNotAllowed):
            await self.broker.execute(
                call,
                OperationContext(write_enabled=True),
            )

        self.assertEqual(self.executed, [])

    async def test_approved_high_risk_operation_reaches_executor(self) -> None:
        call = OperationCall(id="restart-1", name="service.restart", args={})
        result = await self.broker.execute(
            call,
            OperationContext(
                write_enabled=True,
                approval=ApprovalDecision(
                    approved=True,
                    second_confirmation=True,
                ),
            ),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(self.executed, [call])

    def test_registry_rejects_write_operation_without_approval_builder(self) -> None:
        async def execute(call: OperationCall) -> Any:
            return call

        with self.assertRaisesRegex(RuntimeError, "缺少审批展示构造器"):
            OperationRegistry(
                [
                    RegisteredOperation(
                        spec=OperationSpec(
                            name="unsafe.write",
                            effect="write",
                            risk_level="high",
                        ),
                        executor=execute,
                    )
                ]
            )
