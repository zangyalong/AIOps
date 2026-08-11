from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import file_access


class _SessionContext:
    def __init__(self) -> None:
        self.db = object()

    async def __aenter__(self) -> object:
        return self.db

    async def __aexit__(self, *args: object) -> None:
        del args


class GrantServerFileWriteAccessToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_grant_resolves_service_instance_and_syncs_exact_file(self) -> None:
        server = SimpleNamespace(id=uuid.uuid4(), name="web-01")
        service = SimpleNamespace(id=uuid.uuid4(), name="sub2api", environment="prod")
        instance = SimpleNamespace(
            id=uuid.uuid4(),
            service_id=service.id,
            name="sub2api",
            server_id=server.id,
        )
        access = SimpleNamespace(
            id=uuid.uuid4(),
            path="/home/OnlineAPI/deploy/docker-compose.yml",
        )
        create_access = AsyncMock(return_value=access)
        session = _SessionContext()
        with (
            patch.object(file_access, "AsyncSessionLocal", return_value=session),
            patch.object(
                file_access.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                file_access.service_catalog_service,
                "list_instances",
                AsyncMock(return_value=[instance]),
            ),
            patch.object(
                file_access.service_catalog_service,
                "list_services",
                AsyncMock(return_value=[service]),
            ),
            patch.object(
                file_access.server_file_policy_service,
                "create_file_access",
                create_access,
            ),
        ):
            result = await file_access.grant_server_file_write_access.ainvoke(
                {
                    "server_name": "web-01",
                    "service_name": "sub2api",
                    "environment": "prod",
                    "instance_name": "sub2api",
                    "path": access.path,
                }
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.code, "server_file_write_access_granted")
        self.assertEqual(result.metadata["path"], access.path)
        create_access.assert_awaited_once_with(
            session.db,
            instance,
            access_level="write",
            path=access.path,
        )

    async def test_grant_refuses_ambiguous_service_instances(self) -> None:
        server = SimpleNamespace(id=uuid.uuid4(), name="web-01")
        service = SimpleNamespace(id=uuid.uuid4(), name="sub2api", environment="prod")
        instances = [
            SimpleNamespace(
                id=uuid.uuid4(),
                service_id=service.id,
                name=name,
                server_id=server.id,
            )
            for name in ("sub2api-a", "sub2api-b")
        ]
        create_access = AsyncMock()
        with (
            patch.object(
                file_access, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                file_access.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                file_access.service_catalog_service,
                "list_instances",
                AsyncMock(return_value=instances),
            ),
            patch.object(
                file_access.service_catalog_service,
                "list_services",
                AsyncMock(return_value=[service]),
            ),
            patch.object(
                file_access.server_file_policy_service,
                "create_file_access",
                create_access,
            ),
        ):
            result = await file_access.grant_server_file_write_access.ainvoke(
                {
                    "server_name": "web-01",
                    "service_name": "sub2api",
                    "path": "/home/OnlineAPI/deploy/docker-compose.yml",
                }
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "ambiguous_service_instance")
        self.assertIn("instance_name", result.message)
        create_access.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
