from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.agents.tools import alert_rules, catalog
from app.operations.executors import alert_rule as alert_rule_executor
from app.operations.schemas import OperationCall, OperationContext


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class AgentAlertRuleToolTest(unittest.IsolatedAsyncioTestCase):
    def test_tool_schema_exposes_machine_readable_parameter_constraints(self) -> None:
        schema = alert_rules.create_alert_rule.args_schema.model_json_schema()
        properties = schema["properties"]

        self.assertEqual(
            properties["name"]["pattern"],
            "^[A-Za-z_][A-Za-z0-9_]{0,199}$",
        )
        self.assertIn("机器标识符", properties["name"]["description"])
        self.assertEqual(properties["name"]["examples"], ["NodeCpuUsageHigh"])
        self.assertEqual(
            properties["duration"]["pattern"],
            "^(?:0|[1-9][0-9]*)(?:ms|s|m|h|d|w|y)$",
        )
        self.assertEqual(
            properties["labels"]["propertyNames"]["pattern"],
            "^[A-Za-z_][A-Za-z0-9_]*$",
        )

    async def test_create_alert_rule_reuses_service_validation_and_apply(self) -> None:
        saved = SimpleNamespace(
            name="NodeDown",
            enabled=True,
            severity="critical",
            duration="5m",
        )
        with (
            patch.object(
                alert_rule_executor,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(
                alert_rule_executor.alert_rule_service,
                "create_rule",
                AsyncMock(return_value=saved),
            ) as create_rule,
        ):
            result = await alert_rules.create_alert_rule.ainvoke(
                {
                    "name": "NodeDown",
                    "expression": "up == 0",
                    "summary": "节点不可用",
                    "duration": "5m",
                    "severity": "critical",
                }
            )

        create_rule.assert_awaited_once()
        data = create_rule.await_args.args[1]
        self.assertEqual(data.expression, "up == 0")
        self.assertEqual(data.summary, "节点不可用")
        self.assertTrue(result.ok)
        self.assertIn("已创建预警规则 NodeDown", result.message)

    async def test_create_alert_rule_rejects_invalid_name_before_database(self) -> None:
        with patch.object(
            alert_rule_executor.alert_rule_service, "create_rule", AsyncMock()
        ) as create_rule:
            with self.assertRaises(ValidationError):
                await alert_rules.create_alert_rule.ainvoke(
                    {
                        "name": "bad-name",
                        "expression": "up == 0",
                        "summary": "节点不可用",
                    }
                )

        create_rule.assert_not_awaited()

    async def test_broker_rejects_invalid_rule_before_building_approval(self) -> None:
        with self.assertRaises(ValidationError):
            await catalog.OPERATION_BROKER.aprepare(
                OperationCall(
                    id="rule-invalid",
                    name="create_alert_rule",
                    args={
                        "name": "CPU 占用过高",
                        "expression": "up == 0",
                        "summary": "节点不可用",
                    },
                ),
                OperationContext(
                    actor_id="operator-1",
                    actor_role="operator",
                    run_mode="interactive",
                ),
            )


if __name__ == "__main__":
    unittest.main()
