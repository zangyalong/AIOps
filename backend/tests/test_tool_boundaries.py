from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import actions, catalog


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class _FakeConnection:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def run(self, command: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.commands.append(command)
        if "restart" in command:
            return SimpleNamespace(exit_status=0, stdout="", stderr="")
        return SimpleNamespace(exit_status=0, stdout="active\n", stderr="")


class _ConnectionContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        del args


class ToolBoundaryTest(unittest.IsolatedAsyncioTestCase):
    def test_catalog_has_disjoint_read_and_write_tools(self) -> None:
        """目录必须把诊断命令归为读、服务重启归为写，且写工具带审批元数据。"""
        self.assertIn("run_diagnostic_command", catalog.READ_TOOLS_BY_NAME)
        self.assertIn("query_recent_changes", catalog.READ_TOOLS_BY_NAME)
        self.assertNotIn("run_diagnostic_command", catalog.WRITE_TOOLS_BY_NAME)
        self.assertNotIn("query_recent_changes", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("restart_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertFalse(
            set(catalog.READ_TOOLS_BY_NAME) & set(catalog.WRITE_TOOLS_BY_NAME)
        )

        action = catalog.describe_write_action(
            {
                "id": "restart-1",
                "name": "restart_service",
                "args": {"server_name": "demo", "service_name": "nginx"},
            }
        )
        self.assertEqual(action["risk_level"], "high")
        self.assertEqual(action["kind"], "service_restart")
        self.assertEqual(action["server_name"], "demo")

    async def test_restart_service_rejects_empty_allowlist_before_ssh(self) -> None:
        """默认空白名单时，即使审批层误放行，工具自身也不能连接 SSH。"""
        with (
            patch.object(actions.settings, "ssh_restart_service_allowlist", ""),
            patch.object(actions.asyncssh, "connect") as connect,
        ):
            result = await actions.restart_service.ainvoke(
                {"server_name": "demo", "service_name": "nginx"}
            )

        self.assertIn("白名单为空", result)
        connect.assert_not_called()

    async def test_restart_service_executes_allowlisted_service(self) -> None:
        """白名单服务通过校验后，只执行固定 restart 和 is-active 两条命令。"""
        server = SimpleNamespace(name="demo", ip_address="127.0.0.1")
        params = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            username="ops",
            client_keys=["test-key"],
        )
        connection = _FakeConnection()

        with (
            patch.object(actions.settings, "ssh_restart_service_allowlist", "nginx"),
            patch.object(actions, "AsyncSessionLocal", return_value=_SessionContext()),
            patch.object(
                actions.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(actions, "build_connect_params", return_value=params),
            patch.object(
                actions.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
        ):
            result = await actions.restart_service.ainvoke(
                {"server_name": "demo", "service_name": "nginx"}
            )

        self.assertEqual(
            connection.commands,
            [
                "sudo -n -- systemctl restart -- nginx",
                "systemctl is-active -- nginx",
            ],
        )
        self.assertIn("当前状态：active", result)
