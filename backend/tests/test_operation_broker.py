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
                    RegisteredOperation(
                        spec=OperationSpec(
                            name="catalog.register",
                            effect="write",
                            risk_level="medium",
                            environment_arg="environment",
                        ),
                        executor=execute,
                        build_approval_view=lambda call: {
                            "tool_call_id": call.id,
                            "title": "注册服务",
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
            OperationContext(
                actor_id="operator-1",
                actor_role="operator",
                run_mode="interactive",
            ),
        )

        self.assertEqual(prepared.decision.result, "approval_required")
        self.assertEqual(prepared.approval_view["tool_call_id"], "restart-1")
        self.assertEqual(self.executed, [])

    async def test_execute_routes_allowed_read_operation(self) -> None:
        call = OperationCall(id="query-1", name="server.query", args={})
        result = await self.broker.execute(
            call,
            OperationContext(
                actor_id="worker",
                actor_role="system",
                run_mode="unattended",
            ),
        )

        self.assertEqual(result, {"ok": True, "name": "server.query"})
        self.assertEqual(self.executed, [call])

    async def test_broker_classifies_explicit_environment_before_policy(self) -> None:
        prepared = self.broker.prepare(
            OperationCall(
                id="register-1",
                name="catalog.register",
                args={"environment": "staging-cn"},
            ),
            OperationContext(
                actor_id="admin-1",
                actor_role="admin",
                run_mode="interactive",
                auto_approve_non_production_medium=True,
            ),
        )

        self.assertEqual(prepared.context.environment, "non_production")
        self.assertEqual(prepared.decision.result, "allow")
        self.assertEqual(prepared.approval_view["tool_call_id"], "register-1")

    async def test_undeclared_environment_argument_cannot_change_policy_context(
        self,
    ) -> None:
        prepared = self.broker.prepare(
            OperationCall(
                id="restart-1",
                name="service.restart",
                args={"service": "nginx.service", "environment": "dev"},
            ),
            OperationContext(
                actor_id="admin-1",
                actor_role="admin",
                run_mode="interactive",
                environment="production",
                auto_approve_non_production_medium=True,
            ),
        )

        self.assertEqual(prepared.context.environment, "production")
        self.assertEqual(prepared.decision.result, "approval_required")

    async def test_execute_rechecks_policy_before_dispatch(self) -> None:
        call = OperationCall(id="restart-1", name="service.restart", args={})

        with self.assertRaises(OperationNotAllowed):
            await self.broker.execute(
                call,
                OperationContext(
                    actor_id="operator-1",
                    actor_role="operator",
                    run_mode="interactive",
                ),
            )

        self.assertEqual(self.executed, [])

    async def test_approved_high_risk_operation_reaches_executor(self) -> None:
        call = OperationCall(id="restart-1", name="service.restart", args={})
        result = await self.broker.execute(
            call,
            OperationContext(
                actor_id="operator-1",
                actor_role="operator",
                run_mode="interactive",
                approval=ApprovalDecision(
                    approved=True,
                    second_confirmation=True,
                    approver_id="approver-1",
                    approver_role="admin",
                ),
            ),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(self.executed, [call])

    async def test_execute_prepared_dispatches_the_frozen_call(self) -> None:
        call = OperationCall(id="query-1", name="server.query", args={"frozen": True})
        prepared = self.broker.prepare(
            call,
            OperationContext(
                actor_id="worker",
                actor_role="system",
                run_mode="unattended",
            ),
        )

        result = await self.broker.execute_prepared(prepared)

        self.assertTrue(result["ok"])
        self.assertEqual(self.executed, [call])

    async def test_resolves_business_arguments_before_building_approval(self) -> None:
        async def resolve(
            call: OperationCall, context: OperationContext
        ) -> OperationCall:
            del context
            args = call.normalized_args()
            args["runtime_name"] = f"{args['service']}.service"
            return OperationCall(id=call.id, name=call.name, args=args)

        async def execute(call: OperationCall) -> Any:
            return call.normalized_args()

        broker = OperationBroker(
            OperationRegistry(
                [
                    RegisteredOperation(
                        spec=OperationSpec(
                            name="service.start",
                            effect="write",
                            risk_level="medium",
                        ),
                        executor=execute,
                        resolve_call=resolve,
                        build_approval_view=lambda call: {
                            "runtime_name": call.normalized_args()["runtime_name"]
                        },
                    )
                ]
            )
        )

        prepared = await broker.aprepare(
            OperationCall(
                id="start-1",
                name="service.start",
                args={"service": "example-web"},
            ),
            OperationContext(
                actor_id="operator-1",
                actor_role="operator",
                run_mode="interactive",
            ),
        )

        self.assertEqual(
            prepared.call.normalized_args()["runtime_name"],
            "example-web.service",
        )
        self.assertEqual(
            prepared.approval_view,
            {"runtime_name": "example-web.service"},
        )

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
