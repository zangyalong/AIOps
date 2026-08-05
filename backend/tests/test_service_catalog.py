from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import service_topology
from app.schemas.service_catalog import ServiceInstanceCreate
from app.services import service_catalog_service


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


def _service(name: str, environment: str = "prod") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        environment=environment,
    )


class ServiceCatalogToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_server_services_lists_registered_instances(self) -> None:
        server = SimpleNamespace(id=uuid.uuid4(), name="web-01", ip_address="10.0.0.11")
        service = _service("web")
        instance = SimpleNamespace(
            service_id=service.id,
            name="web-01-main",
            runtime_type="systemd",
            runtime_name="web.service",
            metric_instance="10.0.0.11:9100",
            log_service="web",
        )

        with (
            patch.object(
                service_topology, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                service_topology.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_topology.service_catalog_service,
                "list_instances",
                AsyncMock(return_value=[instance]),
            ) as list_instances,
            patch.object(
                service_topology.service_catalog_service,
                "list_services",
                AsyncMock(return_value=[service]),
            ),
        ):
            result = await service_topology.query_server_services.ainvoke(
                {"server": "web-01"}
            )

        list_instances.assert_awaited_once()
        self.assertEqual(list_instances.await_args.kwargs, {"server_id": server.id})
        self.assertIn("web/prod", result)
        self.assertIn("实例=web-01-main", result)
        self.assertIn("runtime=systemd/web.service", result)
        self.assertIn("不代表实时进程或容器运行状态", result)


class ServiceInstanceResolutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_identity_requires_bound_server(self) -> None:
        with self.assertRaises(ValueError):
            ServiceInstanceCreate(
                service_id=uuid.uuid4(),
                name="orders-api",
                runtime_type="docker",
                runtime_name="orders-api",
            )

    async def test_runtime_identity_pair_is_required(self) -> None:
        with self.assertRaises(ValueError):
            ServiceInstanceCreate(
                service_id=uuid.uuid4(),
                server_id=uuid.uuid4(),
                name="orders-api",
                runtime_type="docker",
            )

    async def test_metric_instance_resolves_unique_service_instance(self) -> None:
        service = _service("orders")
        instance = SimpleNamespace(service_id=service.id)
        with (
            patch.object(
                service_catalog_service.service_catalog_repository,
                "list_instances_by_metric_instance",
                AsyncMock(return_value=[instance]),
            ),
            patch.object(
                service_catalog_service,
                "get_service",
                AsyncMock(return_value=service),
            ),
        ):
            resolved = await service_catalog_service.resolve_by_metric_instance(
                object(), "orders:9100"
            )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.service, service)
        self.assertEqual(resolved.instance, instance)
