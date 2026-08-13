from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import server_metric_resolution_service as resolution


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


def _server(*, monitor_labels: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="web-01",
        ip_address="139.196.175.226",
        monitor_labels=monitor_labels,
    )


class ServerMetricResolutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_mapping_wins_without_catalog_or_prometheus(self) -> None:
        query = AsyncMock()
        with patch.object(resolution, "prom_query", query):
            instance, error = await resolution.resolve_server_host_metric_instance(
                _server(monitor_labels={"instance": "custom-node:19100"})
            )

        self.assertEqual(instance, "custom-node:19100")
        self.assertIsNone(error)
        query.assert_not_awaited()

    async def test_catalog_node_exporter_is_verified_without_port_guessing(
        self,
    ) -> None:
        node_service = SimpleNamespace(id=uuid.uuid4(), name="node-exporter")
        mysql_service = SimpleNamespace(id=uuid.uuid4(), name="mysql-exporter")
        instances = [
            SimpleNamespace(
                service_id=node_service.id,
                name="host-metrics",
                runtime_name="node_exporter_custom",
                metric_instance="139.196.175.226:19100",
            ),
            SimpleNamespace(
                service_id=mysql_service.id,
                name="mysql-metrics",
                runtime_name="mysqld_exporter",
                metric_instance="139.196.175.226:9104",
            ),
        ]
        query = AsyncMock(
            return_value=[
                {"metric": {"instance": "139.196.175.226:19100"}},
            ]
        )
        with (
            patch.object(
                resolution, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                resolution.service_catalog_service,
                "list_instances",
                AsyncMock(return_value=instances),
            ),
            patch.object(
                resolution.service_catalog_service,
                "list_services",
                AsyncMock(return_value=[node_service, mysql_service]),
            ),
            patch.object(resolution, "prom_query", query),
        ):
            instance, error = await resolution.resolve_server_host_metric_instance(
                _server()
            )

        self.assertEqual(instance, "139.196.175.226:19100")
        self.assertIsNone(error)
        query.assert_awaited_once_with("node_uname_info")

    async def test_stale_catalog_mapping_is_not_treated_as_runtime_evidence(
        self,
    ) -> None:
        node_service = SimpleNamespace(id=uuid.uuid4(), name="node-exporter")
        catalog_instance = SimpleNamespace(
            service_id=node_service.id,
            name="host-metrics",
            runtime_name="node-exporter",
            metric_instance="139.196.175.226:19100",
        )
        rows = [{"metric": {"instance": "139.196.175.226:29100"}}]
        with (
            patch.object(
                resolution, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                resolution.service_catalog_service,
                "list_instances",
                AsyncMock(return_value=[catalog_instance]),
            ),
            patch.object(
                resolution.service_catalog_service,
                "list_services",
                AsyncMock(return_value=[node_service]),
            ),
            patch.object(resolution, "prom_query", AsyncMock(return_value=rows)),
        ):
            instance, error = await resolution.resolve_server_host_metric_instance(
                _server()
            )

        self.assertIsNone(instance)
        self.assertIn("服务目录", error)
        self.assertIn("node_uname_info", error)
        self.assertIn("不一致", error)

    async def test_prometheus_node_metric_identifies_exporter_among_many_targets(
        self,
    ) -> None:
        rows = [
            {"metric": {"instance": "139.196.175.226:9100", "nodename": "web-01"}},
            {"metric": {"instance": "10.0.0.9:9100", "nodename": "other"}},
        ]
        with (
            patch.object(
                resolution, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                resolution.service_catalog_service,
                "list_instances",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                resolution.service_catalog_service,
                "list_services",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                resolution, "prom_query", AsyncMock(return_value=rows)
            ) as query,
        ):
            instance, error = await resolution.resolve_server_host_metric_instance(
                _server()
            )

        self.assertEqual(instance, "139.196.175.226:9100")
        self.assertIsNone(error)
        query.assert_awaited_once_with("node_uname_info")

    async def test_multiple_host_metric_instances_remain_ambiguous(self) -> None:
        rows = [
            {"metric": {"instance": "139.196.175.226:19100"}},
            {"metric": {"instance": "139.196.175.226:29100"}},
        ]
        with (
            patch.object(
                resolution, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                resolution.service_catalog_service,
                "list_instances",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                resolution.service_catalog_service,
                "list_services",
                AsyncMock(return_value=[]),
            ),
            patch.object(resolution, "prom_query", AsyncMock(return_value=rows)),
        ):
            instance, error = await resolution.resolve_server_host_metric_instance(
                _server()
            )

        self.assertIsNone(instance)
        self.assertIn("node_uname_info", error)
        self.assertIn("多个主机指标实例", error)


if __name__ == "__main__":
    unittest.main()
