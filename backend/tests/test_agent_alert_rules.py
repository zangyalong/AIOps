from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import alert_rules


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class AgentAlertRuleToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_alert_rule_reuses_service_validation_and_apply(self) -> None:
        saved = SimpleNamespace(
            name="NodeDown",
            enabled=True,
            severity="critical",
            duration="5m",
        )
        with (
            patch.object(
                alert_rules, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                alert_rules.alert_rule_service,
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
        self.assertIn("已创建预警规则 NodeDown", result)

    async def test_create_alert_rule_rejects_invalid_name_before_database(self) -> None:
        with patch.object(
            alert_rules.alert_rule_service, "create_rule", AsyncMock()
        ) as create_rule:
            result = await alert_rules.create_alert_rule.ainvoke(
                {
                    "name": "bad-name",
                    "expression": "up == 0",
                    "summary": "节点不可用",
                }
            )

        create_rule.assert_not_awaited()
        self.assertIn("参数校验失败", result)


if __name__ == "__main__":
    unittest.main()
