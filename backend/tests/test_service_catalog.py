from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import service_topology
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
        owner=None,
    )


class ServiceTopologyTraversalTest(unittest.IsolatedAsyncioTestCase):
    async def test_topology_returns_connected_component_with_depth_limit(self) -> None:
        orders = _service("orders")
        payments = _service("payments")
        database = _service("mysql")
        unrelated = _service("search")
        edges = [
            SimpleNamespace(
                id=uuid.uuid4(),
                upstream_service_id=orders.id,
                downstream_service_id=payments.id,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                upstream_service_id=payments.id,
                downstream_service_id=database.id,
            ),
        ]

        with (
            patch.object(
                service_catalog_service,
                "get_service",
                AsyncMock(return_value=orders),
            ),
            patch.object(
                service_catalog_service,
                "list_services",
                AsyncMock(return_value=[orders, payments, database, unrelated]),
            ),
            patch.object(
                service_catalog_service,
                "list_dependencies",
                AsyncMock(return_value=edges),
            ),
        ):
            topology = await service_catalog_service.get_topology(
                object(), orders.id, max_depth=1
            )

        self.assertEqual({item.id for item in topology.nodes}, {orders.id, payments.id})
        self.assertEqual(len(topology.edges), 1)

    async def test_self_dependency_is_rejected_before_database_lookup(self) -> None:
        service_id = uuid.uuid4()
        with self.assertRaises(service_catalog_service.InvalidDependency):
            await service_catalog_service._validate_dependency(
                object(), service_id, service_id
            )


class ServiceTopologyToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_impacted_services_walks_reverse_dependencies(self) -> None:
        orders = _service("orders")
        payments = _service("payments")
        database = _service("mysql")
        edges = [
            SimpleNamespace(
                upstream_service_id=orders.id,
                downstream_service_id=payments.id,
            ),
            SimpleNamespace(
                upstream_service_id=payments.id,
                downstream_service_id=database.id,
            ),
        ]

        with (
            patch.object(
                service_topology, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                service_topology.service_catalog_service,
                "resolve_service",
                AsyncMock(return_value=database),
            ),
            patch.object(
                service_topology.service_catalog_service,
                "list_services",
                AsyncMock(return_value=[orders, payments, database]),
            ),
            patch.object(
                service_topology.service_catalog_service,
                "list_dependencies",
                AsyncMock(return_value=edges),
            ),
        ):
            result = await service_topology.query_impacted_services.ainvoke(
                {"service": "mysql", "environment": "prod", "max_depth": 3}
            )

        self.assertIn("第 1 层：payments/prod", result)
        self.assertIn("第 2 层：orders/prod", result)

    async def test_upstream_candidates_walks_service_dependencies(self) -> None:
        orders = _service("orders")
        payments = _service("payments")
        database = _service("mysql")
        edges = [
            SimpleNamespace(
                upstream_service_id=orders.id,
                downstream_service_id=payments.id,
            ),
            SimpleNamespace(
                upstream_service_id=payments.id,
                downstream_service_id=database.id,
            ),
        ]

        with (
            patch.object(
                service_topology, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                service_topology.service_catalog_service,
                "resolve_service",
                AsyncMock(return_value=orders),
            ),
            patch.object(
                service_topology.service_catalog_service,
                "list_services",
                AsyncMock(return_value=[orders, payments, database]),
            ),
            patch.object(
                service_topology.service_catalog_service,
                "list_dependencies",
                AsyncMock(return_value=edges),
            ),
        ):
            result = await service_topology.query_upstream_candidates.ainvoke(
                {"service": "orders", "environment": "prod", "max_depth": 3}
            )

        self.assertIn("第 1 层：payments/prod", result)
        self.assertIn("第 2 层：mysql/prod", result)


class ServiceInstanceResolutionTest(unittest.IsolatedAsyncioTestCase):
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
