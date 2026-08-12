from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import service_control_policy_service


class _FakeConnection:
    def __init__(self, *, exit_status: int = 0) -> None:
        self.exit_status = exit_status
        self.commands: list[str] = []

    async def run(self, command: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.commands.append(command)
        return SimpleNamespace(
            exit_status=self.exit_status,
            stdout="已同步服务控制白名单。\n" if self.exit_status == 0 else "",
            stderr="网关拒绝\n" if self.exit_status else "",
        )


class _ConnectionContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        del args


def _server() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="web-01",
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_user="aioms",
        ssh_key_filename="web-01-aioms",
    )


def _instance(server_id: uuid.UUID, *, enabled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        server_id=server_id,
        runtime_type="systemd",
        runtime_name="mysqld.service",
        control_enabled=enabled,
    )


class ServiceControlPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_enable_syncs_remote_before_database(self) -> None:
        server = _server()
        instance = _instance(server.id)
        connection = _FakeConnection()
        saved = SimpleNamespace(control_enabled=True)
        db = object()

        with (
            patch.object(
                service_control_policy_service.server_service,
                "get_server",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control_policy_service,
                "build_connect_params",
                return_value=SimpleNamespace(
                    host=server.ip_address,
                    port=22,
                    username="aioms",
                    client_keys=["test-key"],
                ),
            ),
            patch.object(
                service_control_policy_service.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
            patch.object(
                service_control_policy_service.service_catalog_service,
                "set_control_authorization",
                AsyncMock(return_value=saved),
            ) as save_authorization,
        ):
            result = await service_control_policy_service.update_control_authorization(
                db, instance, enabled=True
            )

        self.assertEqual(result, saved)
        self.assertEqual(
            connection.commands,
            [
                "sudo -n -- /usr/local/sbin/aioms-service-policy "
                "add systemd mysqld.service"
            ],
        )
        save_authorization.assert_awaited_once_with(db, instance, enabled=True)

    async def test_remote_rejection_keeps_database_unchanged(self) -> None:
        server = _server()
        instance = _instance(server.id)
        connection = _FakeConnection(exit_status=1)

        with (
            patch.object(
                service_control_policy_service.server_service,
                "get_server",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control_policy_service,
                "build_connect_params",
                return_value=SimpleNamespace(
                    host=server.ip_address,
                    port=22,
                    username="aioms",
                    client_keys=["test-key"],
                ),
            ),
            patch.object(
                service_control_policy_service.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
            patch.object(
                service_control_policy_service.service_catalog_service,
                "set_control_authorization",
                AsyncMock(),
            ) as save_authorization,
        ):
            with self.assertRaisesRegex(
                service_control_policy_service.ServiceControlPolicySyncError,
                "网关拒绝",
            ):
                await service_control_policy_service.update_control_authorization(
                    object(), instance, enabled=True
                )

        save_authorization.assert_not_awaited()
        self.assertFalse(instance.control_enabled)

    async def test_database_failure_rolls_back_remote_policy(self) -> None:
        server = _server()
        instance = _instance(server.id)
        connection = _FakeConnection()

        with (
            patch.object(
                service_control_policy_service.server_service,
                "get_server",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control_policy_service,
                "build_connect_params",
                return_value=SimpleNamespace(
                    host=server.ip_address,
                    port=22,
                    username="aioms",
                    client_keys=["test-key"],
                ),
            ),
            patch.object(
                service_control_policy_service.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
            patch.object(
                service_control_policy_service.service_catalog_service,
                "set_control_authorization",
                AsyncMock(side_effect=RuntimeError("database failed")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "database failed"):
                await service_control_policy_service.update_control_authorization(
                    object(), instance, enabled=True
                )

        self.assertEqual(
            connection.commands,
            [
                "sudo -n -- /usr/local/sbin/aioms-service-policy "
                "add systemd mysqld.service",
                "sudo -n -- /usr/local/sbin/aioms-service-policy "
                "remove systemd mysqld.service",
            ],
        )
        self.assertFalse(instance.control_enabled)

    async def test_docker_authorization_uses_same_policy_gateway(self) -> None:
        server = _server()
        instance = _instance(server.id)
        instance.runtime_type = "docker"
        instance.runtime_name = "orders-api"
        connection = _FakeConnection()

        with (
            patch.object(
                service_control_policy_service.server_service,
                "get_server",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control_policy_service,
                "build_connect_params",
                return_value=SimpleNamespace(
                    host=server.ip_address,
                    port=22,
                    username="aioms",
                    client_keys=["test-key"],
                ),
            ),
            patch.object(
                service_control_policy_service.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
            patch.object(
                service_control_policy_service.service_catalog_service,
                "set_control_authorization",
                AsyncMock(return_value=instance),
            ),
        ):
            await service_control_policy_service.update_control_authorization(
                object(), instance, enabled=True
            )

        self.assertEqual(
            connection.commands,
            ["sudo -n -- /usr/local/sbin/aioms-service-policy add docker orders-api"],
        )
