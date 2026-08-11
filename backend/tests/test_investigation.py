from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import investigation
from app.operations.schemas import OperationResult


class InvestigationFacadeTest(unittest.IsolatedAsyncioTestCase):
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
        result = await investigation.investigate.ainvoke(
            {
                "area": "metrics",
                "target": "web-01",
                "query": "cpu",
                "parameters": {"minutes": 60},
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_investigation_request")
        self.assertIn("不支持 parameters.minutes", result.message)

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
                        "path": "/sub2api-deploy/.env",
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
                "path": "/sub2api-deploy/.env",
                "config_format": "dotenv",
                "selector": "SERVER_PORT",
                "value": "8081",
            }
        )


if __name__ == "__main__":
    unittest.main()
