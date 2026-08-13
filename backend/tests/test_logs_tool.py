from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.agents.tools import logs


class LogsToolCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_logs_merges_new_and_legacy_labels_without_duplicates(
        self,
    ) -> None:
        alloy_stream = {
            "stream": {
                "service": "orders-api",
                "service_name": "orders-api",
                "server": "web01",
            },
            "values": [["1000000000", "INFO request completed"]],
        }
        promtail_stream = {
            "stream": {"container": "orders-api"},
            "values": [["2000000000", "WARN legacy stream"]],
        }
        query = AsyncMock(
            side_effect=[
                [alloy_stream],
                [alloy_stream],
                [promtail_stream],
            ]
        )

        with patch.object(logs, "loki_query_range", query):
            result = await logs.search_logs.ainvoke(
                {"server": "web-01", "service": "orders-api", "minutes": 10}
            )

        self.assertEqual(query.await_count, 3)
        selectors = [call.args[0] for call in query.await_args_list]
        self.assertIn('{server="web-01",service="orders-api"}', selectors)
        self.assertIn('{server="web-01",service_name="orders-api"}', selectors)
        self.assertIn('{server="web-01",container="orders-api"}', selectors)
        self.assertEqual(result.count("INFO request completed"), 1)
        self.assertEqual(result.count("WARN legacy stream"), 1)

    async def test_keyword_regex_is_escaped_for_logql_string(self) -> None:
        query = AsyncMock(return_value=[])
        with patch.object(logs, "loki_query_range", query):
            await logs.search_logs.ainvoke(
                {
                    "server": "web-01",
                    "service": "pixel-forge-backend",
                    "keyword": '{service="pixel-forge-backend"}',
                }
            )

        logql = query.await_args_list[0].args[0]
        self.assertIn('server="web-01"', logql)
        self.assertIn('service="pixel-forge-backend"', logql)
        self.assertIn(r'|~ "(?i)\\{service=', logql)
        self.assertNotIn(r'|~ "(?i)\{service=', logql)

    async def test_list_log_services_unions_all_supported_label_names(self) -> None:
        label_values = AsyncMock(
            side_effect=[
                ["orders-api"],
                ["orders-api", "legacy-worker"],
                ["aioms-fake-app"],
            ]
        )

        with patch.object(logs, "loki_label_values", label_values):
            result = await logs.list_log_services.ainvoke({})

        self.assertEqual(label_values.await_count, 3)
        self.assertEqual(result.count("- orders-api"), 1)
        self.assertIn("- legacy-worker", result)
        self.assertIn("- aioms-fake-app", result)
