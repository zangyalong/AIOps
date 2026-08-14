from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import investigation
from app.operations.schemas import OperationResult


class InvestigationFacadeTest(unittest.IsolatedAsyncioTestCase):
    def test_parameters_schema_is_closed_and_describes_metrics_protocol(self) -> None:
        schema = investigation.InvestigationInput.model_json_schema()
        parameters_schema = schema["$defs"]["InvestigationParameters"]
        target_type_schema = parameters_schema["properties"]["target_type"]

        self.assertFalse(parameters_schema["additionalProperties"])
        self.assertEqual(
            target_type_schema["anyOf"][0]["enum"],
            ["server", "instance"],
        )
        self.assertIn(
            "metrics 只允许 target_type",
            schema["properties"]["parameters"]["description"],
        )
        self.assertIn(
            "area=visualization",
            schema["properties"]["query"]["description"],
        )
        self.assertIn(
            "具体服务放 parameters.service",
            schema["properties"]["target"]["description"],
        )

    async def test_host_area_uses_real_host_inspector_and_labels_source(self) -> None:
        inspect = AsyncMock(
            return_value=OperationResult.success(
                "Python 3.6.8",
                metadata={
                    "exit_code": 0,
                    "timed_out": False,
                    "truncated": False,
                },
            )
        )
        with patch.object(investigation, "inspect_host", inspect):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "host",
                    "target": "web-01",
                    "query": "python --version",
                }
            )

        self.assertTrue(result.ok)
        self.assertIn("来源：目标服务器宿主机只读调查", result.message)
        self.assertIn("目标：web-01", result.message)
        self.assertIn("Python 3.6.8", result.message)
        self.assertEqual(result.metadata["evidence_source"], "目标服务器宿主机只读调查")
        inspect.assert_awaited_once_with("web-01", "python --version")

    async def test_logs_routes_server_service_and_keyword_separately(self) -> None:
        search = AsyncMock(return_value="日志结果")
        with patch.object(
            investigation,
            "search_logs",
            SimpleNamespace(ainvoke=search),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "logs",
                    "target": "web-01",
                    "query": "connection refused",
                    "parameters": {
                        "service": "example-api",
                        "minutes": 60,
                    },
                }
            )

        self.assertTrue(result.ok)
        search.assert_awaited_once_with(
            {
                "server": "web-01",
                "service": "example-api",
                "keyword": "connection refused",
                "level": "",
                "minutes": 60,
            }
        )

    async def test_logs_extracts_simple_service_selector_instead_of_keyword(
        self,
    ) -> None:
        search = AsyncMock(return_value="日志结果")
        with patch.object(
            investigation,
            "search_logs",
            SimpleNamespace(ainvoke=search),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "logs",
                    "target": "web-01",
                    "query": '{service="example-api"}',
                    "parameters": {"minutes": 60},
                }
            )

        self.assertTrue(result.ok)
        search.assert_awaited_once_with(
            {
                "server": "web-01",
                "service": "example-api",
                "keyword": "",
                "level": "",
                "minutes": 60,
            }
        )

    async def test_logs_resolves_bare_query_from_real_loki_service_labels(
        self,
    ) -> None:
        search = AsyncMock(return_value="日志结果")
        discover = AsyncMock(return_value={"example-api", "nginx"})
        with (
            patch.object(investigation, "discover_log_services", discover),
            patch.object(
                investigation,
                "search_logs",
                SimpleNamespace(ainvoke=search),
            ),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "logs",
                    "target": "web-01",
                    "query": "example-api",
                    "parameters": {"minutes": 60},
                }
            )

        self.assertTrue(result.ok)
        discover.assert_awaited_once_with()
        search.assert_awaited_once_with(
            {
                "server": "web-01",
                "service": "example-api",
                "keyword": "",
                "level": "",
                "minutes": 60,
            }
        )

    async def test_logs_keeps_unmatched_bare_query_as_body_keyword(self) -> None:
        search = AsyncMock(return_value="日志结果")
        discover = AsyncMock(return_value={"example-api"})
        with (
            patch.object(investigation, "discover_log_services", discover),
            patch.object(
                investigation,
                "search_logs",
                SimpleNamespace(ainvoke=search),
            ),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "logs",
                    "target": "web-01",
                    "query": "connection refused",
                }
            )

        self.assertTrue(result.ok)
        search.assert_awaited_once_with(
            {
                "server": "web-01",
                "service": "",
                "keyword": "connection refused",
                "level": "",
                "minutes": 60,
            }
        )

    async def test_runtime_selector_builds_internal_canonical_probe(self) -> None:
        run_probe = AsyncMock(return_value="active (running)")
        with patch.object(
            investigation,
            "run_diagnostic_command",
            SimpleNamespace(ainvoke=run_probe),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "runtime",
                    "target": "web-01",
                    "query": "service:nginx.service",
                }
            )

        self.assertTrue(result.ok)
        self.assertIn("来源：目标服务器实时状态探针", result.message)
        run_probe.assert_awaited_once_with(
            {
                "server_name": "web-01",
                "command": "systemctl status nginx.service",
            }
        )

    async def test_invalid_runtime_selector_is_a_structured_failure(self) -> None:
        result = await investigation.investigate.ainvoke(
            {
                "area": "runtime",
                "target": "web-01",
                "query": "arbitrary-shell-command",
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_investigation_request")
        self.assertIn("runtime.query", result.message)

    async def test_unknown_parameter_is_rejected_instead_of_ignored(self) -> None:
        for field, value in (
            ("time", "1h"),
            ("minutes", 60),
            ("instance", "192.0.2.10:9100"),
            ("monitor_labels", {"instance": "192.0.2.10:9100"}),
        ):
            with self.subTest(field=field):
                result = await investigation.investigate.ainvoke(
                    {
                        "area": "metrics",
                        "target": "web-01",
                        "query": "cpu",
                        "parameters": {field: value},
                    }
                )

                self.assertFalse(result.ok)
                self.assertEqual(result.code, "invalid_investigation_request")
                self.assertIn(f"不支持 parameters.{field}", result.message)
                self.assertIn('"area":"metrics"', result.message)
                self.assertIn('"target_type":"instance"', result.message)
                self.assertIn('"area":"visualization"', result.message)

    async def test_servers_parameter_error_includes_copyable_call(self) -> None:
        result = await investigation.investigate.ainvoke(
            {"area": "servers", "parameters": {"at": None}}
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_investigation_request")
        self.assertIn('正确调用：{"area":"servers"}', result.message)

    async def test_invalid_metrics_target_type_returns_actionable_correction(
        self,
    ) -> None:
        result = await investigation.investigate.ainvoke(
            {
                "area": "metrics",
                "target": "web-01",
                "query": "memory",
                "parameters": {"target_type": "node"},
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_investigation_request")
        self.assertIn('只能是 "server" 或 "instance"', result.message)
        self.assertIn('不要使用 "node"', result.message)

    async def test_metrics_server_target_is_resolved_by_backend(self) -> None:
        query_server = AsyncMock(return_value="server metric result")
        query_instance = AsyncMock()
        with (
            patch.object(
                investigation,
                "query_server_metrics",
                SimpleNamespace(ainvoke=query_server),
            ),
            patch.object(
                investigation,
                "query_metrics",
                SimpleNamespace(ainvoke=query_instance),
            ),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "metrics",
                    "target": "web-01",
                    "query": "memory",
                    "parameters": {},
                }
            )

        self.assertTrue(result.ok)
        query_server.assert_awaited_once_with({"server": "web-01", "metric": "memory"})
        query_instance.assert_not_awaited()

    async def test_metrics_instance_target_uses_explicit_instance_mode(self) -> None:
        query_server = AsyncMock()
        query_instance = AsyncMock(return_value="instance metric result")
        with (
            patch.object(
                investigation,
                "query_server_metrics",
                SimpleNamespace(ainvoke=query_server),
            ),
            patch.object(
                investigation,
                "query_metrics",
                SimpleNamespace(ainvoke=query_instance),
            ),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "metrics",
                    "target": "192.0.2.10:9100",
                    "query": "memory",
                    "parameters": {"target_type": "instance"},
                }
            )

        self.assertTrue(result.ok)
        query_server.assert_not_awaited()
        query_instance.assert_awaited_once_with(
            {
                "metric": "memory",
                "instance": "192.0.2.10:9100",
            }
        )

    async def test_metrics_trend_uses_visualization_route(self) -> None:
        grafana_link = AsyncMock(return_value="http://localhost:3000/explore")
        resolve_instance = AsyncMock(
            return_value=(
                SimpleNamespace(name="web-01"),
                "192.0.2.10:9100",
                None,
            )
        )
        with (
            patch.object(
                investigation,
                "grafana_explore_link",
                SimpleNamespace(ainvoke=grafana_link),
            ),
            patch.object(
                investigation,
                "resolve_server_metric_instance",
                resolve_instance,
            ),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "visualization",
                    "target": "web-01",
                    "query": "memory",
                    "parameters": {"kind": "metrics", "minutes": 60},
                }
            )

        self.assertTrue(result.ok)
        self.assertIn(
            "web-01 → 192.0.2.10:9100",
            result.message,
        )
        resolve_instance.assert_awaited_once_with("web-01")
        grafana_link.assert_awaited_once_with(
            {
                "kind": "metrics",
                "query": "memory",
                "minutes": 60,
                "instance": "192.0.2.10:9100",
            }
        )

    async def test_metrics_trend_reports_instance_resolution_failure(self) -> None:
        grafana_link = AsyncMock()
        with (
            patch.object(
                investigation,
                "grafana_explore_link",
                SimpleNamespace(ainvoke=grafana_link),
            ),
            patch.object(
                investigation,
                "resolve_server_metric_instance",
                AsyncMock(return_value=(None, None, "没有匹配的 node_exporter")),
            ),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "visualization",
                    "target": "web-01",
                    "query": "memory",
                    "parameters": {"kind": "metrics", "minutes": 60},
                }
            )

        self.assertFalse(result.ok)
        self.assertIn("没有匹配的 node_exporter", result.message)
        self.assertEqual(result.code, "investigation_metric_instance_unavailable")
        grafana_link.assert_not_awaited()

    async def test_metrics_trend_does_not_rewrite_raw_promql_for_server(self) -> None:
        grafana_link = AsyncMock()
        with patch.object(
            investigation,
            "grafana_explore_link",
            SimpleNamespace(ainvoke=grafana_link),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "visualization",
                    "target": "web-01",
                    "query": "node_memory_MemTotal_bytes",
                    "parameters": {"kind": "metrics", "minutes": 60},
                }
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_investigation_request")
        self.assertIn("原始 PromQL 请自行写入 instance 选择器", result.message)
        grafana_link.assert_not_awaited()

    async def test_service_area_hides_catalog_and_discovery_tools(self) -> None:
        catalog = AsyncMock(return_value="目录证据")
        discover = AsyncMock(return_value="实时证据")
        with (
            patch.object(
                investigation,
                "query_server_services",
                SimpleNamespace(ainvoke=catalog),
            ),
            patch.object(
                investigation,
                "discover_server_services",
                SimpleNamespace(ainvoke=discover),
            ),
        ):
            catalog_result = await investigation.investigate.ainvoke(
                {"area": "services", "target": "web-01", "query": "catalog"}
            )
            runtime_result = await investigation.investigate.ainvoke(
                {"area": "services", "target": "web-01", "query": "discover"}
            )

        self.assertTrue(catalog_result.ok)
        self.assertTrue(runtime_result.ok)
        catalog.assert_awaited_once_with({"server": "web-01"})
        discover.assert_awaited_once_with({"server_name": "web-01"})

    async def test_change_area_only_returns_preview_evidence(self) -> None:
        preview = AsyncMock(return_value='{"preview_hash":"abc"}')
        with patch.object(
            investigation,
            "preview_server_log_config",
            SimpleNamespace(ainvoke=preview),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "change",
                    "target": "web-01",
                    "query": "log-config-preview",
                }
            )

        self.assertTrue(result.ok)
        self.assertIn("来源：受控变更预览", result.message)
        preview.assert_awaited_once_with({"server_name": "web-01"})

    async def test_change_area_routes_structured_config_preview(self) -> None:
        preview = AsyncMock(return_value='{"preview_hash":"abc"}')
        with patch.object(
            investigation,
            "preview_server_config_patch",
            SimpleNamespace(ainvoke=preview),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "change",
                    "target": "web-01",
                    "query": "config-patch-preview",
                    "parameters": {
                        "path": "/example-api-deploy/.env",
                        "config_format": "dotenv",
                        "selector": "SERVER_PORT",
                        "value": "8081",
                    },
                }
            )

        self.assertTrue(result.ok)
        preview.assert_awaited_once_with(
            {
                "server_name": "web-01",
                "path": "/example-api-deploy/.env",
                "config_format": "dotenv",
                "selector": "SERVER_PORT",
                "value": "8081",
            }
        )

    async def test_change_area_rejects_parameters_for_another_preview_type(
        self,
    ) -> None:
        preview = AsyncMock()
        with patch.object(
            investigation,
            "preview_server_file_update",
            SimpleNamespace(ainvoke=preview),
        ):
            result = await investigation.investigate.ainvoke(
                {
                    "area": "change",
                    "target": "web-01",
                    "query": "file-update-preview",
                    "parameters": {
                        "path": "/example-api-deploy/.env",
                        "config_format": "dotenv",
                        "selector": "SERVER_PORT",
                        "value": "8081",
                    },
                }
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_investigation_request")
        self.assertIn("config-patch-preview", result.message)
        preview.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
