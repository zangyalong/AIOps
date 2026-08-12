from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import metrics


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class QueryServerMetricsTest(unittest.IsolatedAsyncioTestCase):
    def test_empty_server_ip_does_not_match_arbitrary_instances(self) -> None:
        selected, matches = metrics.select_server_metric_instance(
            "",
            ["10.0.0.8:9100"],
        )

        self.assertIsNone(selected)
        self.assertEqual(matches, ())

    def test_promql_template_can_be_scoped_to_exact_instance(self) -> None:
        memory = metrics.promql_for_metric("memory", "10.0.0.8:9100")
        cpu = metrics.promql_for_metric("cpu", "10.0.0.8:9100")

        self.assertEqual(memory.count('instance="10.0.0.8:9100"'), 2)
        self.assertIn('mode="idle",instance="10.0.0.8:9100"', cpu)

    async def test_uses_configured_instance_without_discovery(self) -> None:
        """服务器已有 instance 映射时，一次工具调用直接查询指标，不再枚举实例。"""
        server = SimpleNamespace(
            name="docker-test-01",
            ip_address="127.0.0.1",
            monitor_labels={"instance": "127.0.0.1:9100"},
        )
        find_server = AsyncMock(return_value=server)
        query = AsyncMock(return_value="内存使用率：42.0%")
        discover = AsyncMock(return_value=["不应调用:9100"])

        with (
            patch.object(metrics, "AsyncSessionLocal", return_value=_SessionContext()),
            patch.object(
                metrics.server_service,
                "get_server_by_name_or_ip",
                find_server,
            ),
            patch.object(metrics, "_query_metrics_text", query),
            patch.object(metrics, "prom_label_values", discover),
        ):
            result = await metrics.query_server_metrics.ainvoke(
                {"server": "docker-test-01", "metric": "memory"}
            )

        self.assertIn("docker-test-01 (127.0.0.1)", result)
        self.assertIn("instance=127.0.0.1:9100", result)
        self.assertIn("内存使用率：42.0%", result)
        query.assert_awaited_once_with("memory", "127.0.0.1:9100")
        discover.assert_not_awaited()

    async def test_discovers_instance_by_ip_when_mapping_is_missing(self) -> None:
        """未配置 monitor_labels 时，在同一次工具调用内部按 IP 补全 instance。"""
        server = SimpleNamespace(
            name="demo",
            ip_address="10.0.0.8",
            monitor_labels=None,
        )
        query = AsyncMock(return_value="CPU 使用率：8.0%")

        with (
            patch.object(metrics, "AsyncSessionLocal", return_value=_SessionContext()),
            patch.object(
                metrics.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(metrics, "_query_metrics_text", query),
            patch.object(
                metrics,
                "prom_label_values",
                AsyncMock(return_value=["10.0.0.8:9100", "10.0.0.9:9100"]),
            ),
        ):
            result = await metrics.query_server_metrics.ainvoke(
                {"server": "10.0.0.8", "metric": "cpu"}
            )

        self.assertIn("instance=10.0.0.8:9100", result)
        query.assert_awaited_once_with("cpu", "10.0.0.8:9100")

    async def test_standard_node_exporter_wins_over_other_exporters(self) -> None:
        """同一服务器有多个 exporter 时，主机指标必须确定性选择标准 9100。"""
        server = SimpleNamespace(
            name="web-01",
            ip_address="139.196.175.226",
            monitor_labels=None,
        )
        with patch.object(
            metrics,
            "prom_label_values",
            AsyncMock(
                return_value=[
                    "139.196.175.226:8080",
                    "139.196.175.226:9100",
                    "139.196.175.226:9104",
                    "139.196.175.226:9121",
                    "139.196.175.226:9187",
                ]
            ),
        ):
            instance, error = await metrics._resolve_metric_instance(server)

        self.assertEqual(instance, "139.196.175.226:9100")
        self.assertIsNone(error)

    async def test_multiple_nonstandard_exporters_remain_ambiguous(self) -> None:
        server = SimpleNamespace(
            name="web-01",
            ip_address="139.196.175.226",
            monitor_labels=None,
        )
        with patch.object(
            metrics,
            "prom_label_values",
            AsyncMock(
                return_value=[
                    "139.196.175.226:9104",
                    "139.196.175.226:9187",
                ]
            ),
        ):
            instance, error = await metrics._resolve_metric_instance(server)

        self.assertIsNone(instance)
        self.assertIn("没有标准的 node_exporter", error)
