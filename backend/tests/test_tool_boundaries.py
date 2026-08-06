from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.graphs import chat_graph
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
        action = command.split()[-2]
        service = command.split()[-1]
        state = "inactive" if action == "stop" else "active"
        return SimpleNamespace(
            exit_status=0,
            stdout=f"已{action}服务 {service}；当前状态：{state}。\n",
            stderr="",
        )


class _ConnectionContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        del args


class ToolBoundaryTest(unittest.IsolatedAsyncioTestCase):
    def test_catalog_has_disjoint_read_and_write_tools(self) -> None:
        """目录必须把发现归为读、目录注册/服务重启归为写，且写工具带审批元数据。"""
        self.assertIn("run_diagnostic_command", catalog.READ_TOOLS_BY_NAME)
        self.assertIn("discover_server_services", catalog.READ_TOOLS_BY_NAME)
        self.assertIn("preview_server_log_config", catalog.READ_TOOLS_BY_NAME)
        self.assertIn("query_server_services", catalog.READ_TOOLS_BY_NAME)
        self.assertNotIn("run_diagnostic_command", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("restart_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("start_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("stop_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("create_alert_rule", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("register_server_services", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("apply_server_log_config", catalog.WRITE_TOOLS_BY_NAME)
        self.assertFalse(
            set(catalog.READ_TOOLS_BY_NAME) & set(catalog.WRITE_TOOLS_BY_NAME)
        )
        self.assertIn(
            "query_server_services",
            {
                tool.name
                for tool in chat_graph.SPECIALIST_READ_TOOLS["infrastructure_agent"]
            },
        )
        self.assertIn(
            "discover_server_services",
            {
                tool.name
                for tool in chat_graph.SPECIALIST_READ_TOOLS["infrastructure_agent"]
            },
        )
        self.assertIn(
            "register_server_services",
            {
                tool.name
                for tool in chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"]
            },
        )
        self.assertIn(
            "apply_server_log_config",
            {
                tool.name
                for tool in chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"]
            },
        )
        self.assertIn(
            "create_alert_rule",
            {tool.name for tool in chat_graph.SPECIALIST_WRITE_TOOLS["incident_agent"]},
        )
        self.assertIn("必须使用简体中文", chat_graph.SIMPLIFIED_CHINESE_REQUIREMENT)

        action = catalog.describe_write_action(
            {
                "id": "restart-1",
                "name": "restart_service",
                "args": {"server_name": "demo", "service_name": "nginx.service"},
            }
        )
        self.assertEqual(action["risk_level"], "high")
        self.assertEqual(action["kind"], "service_restart")
        self.assertEqual(action["server_name"], "demo")
        self.assertIn("aioms-service-control restart nginx.service", action["command"])

        stop_action = catalog.describe_write_action(
            {
                "id": "stop-1",
                "name": "stop_service",
                "args": {"server_name": "demo", "service_name": "bt.service"},
            }
        )
        self.assertEqual(stop_action["risk_level"], "high")
        self.assertEqual(stop_action["kind"], "service_stop")
        self.assertIn("aioms-service-control stop bt.service", stop_action["command"])

        rule_action = catalog.describe_write_action(
            {
                "id": "rule-1",
                "name": "create_alert_rule",
                "args": {
                    "name": "NodeDown",
                    "expression": "up == 0",
                    "duration": "5m",
                    "severity": "critical",
                    "summary": "节点不可用",
                },
            }
        )
        self.assertEqual(rule_action["risk_level"], "medium")
        self.assertEqual(rule_action["kind"], "alert_rule_create")
        self.assertIn("up == 0", rule_action["command"])

        registration_action = catalog.describe_write_action(
            {
                "id": "register-1",
                "name": "register_server_services",
                "args": {
                    "server_name": "web01",
                    "environment": "prod",
                    "services": [
                        {
                            "service_name": "orders-api",
                            "runtime_type": "docker",
                            "runtime_name": "orders-api",
                        }
                    ],
                },
            }
        )
        self.assertEqual(registration_action["risk_level"], "medium")
        self.assertEqual(registration_action["kind"], "service_catalog_registration")
        self.assertIn("orders-api ← docker/orders-api", registration_action["command"])

        log_action = catalog.describe_write_action(
            {
                "id": "alloy-1",
                "name": "apply_server_log_config",
                "args": {
                    "server_name": "web01",
                    "preview_hash": "a" * 64,
                    "mappings": [
                        {
                            "service_name": "orders-api",
                            "environment": "prod",
                            "runtime_type": "docker",
                            "runtime_name": "orders-api",
                            "log_source": "docker",
                        }
                    ],
                },
            }
        )
        self.assertEqual(log_action["risk_level"], "high")
        self.assertEqual(log_action["kind"], "server_log_config_apply")
        self.assertIn("orders-api ← docker/orders-api", log_action["command"])

    async def test_restart_service_rejects_empty_allowlist_before_ssh(self) -> None:
        """默认空白名单时，即使审批层误放行，工具自身也不能连接 SSH。"""
        with (
            patch.object(actions.settings, "ssh_service_control_allowlist", ""),
            patch.object(actions.settings, "ssh_restart_service_allowlist", ""),
            patch.object(actions.asyncssh, "connect") as connect,
        ):
            result = await actions.restart_service.ainvoke(
                {"server_name": "demo", "service_name": "nginx.service"}
            )

        self.assertIn("白名单为空", result)
        connect.assert_not_called()

    async def test_service_control_executes_fixed_gateway_with_deploy_identity(
        self,
    ) -> None:
        """三种动作都只能通过部署身份调用固定网关。"""
        server = SimpleNamespace(name="demo", ip_address="127.0.0.1")
        params = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            username="ops",
            client_keys=["test-key"],
        )
        connection = _FakeConnection()

        with (
            patch.object(
                actions.settings,
                "ssh_service_control_allowlist",
                "nginx.service,bt.service",
            ),
            patch.object(actions, "AsyncSessionLocal", return_value=_SessionContext()),
            patch.object(
                actions.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(actions, "build_deploy_connect_params", return_value=params),
            patch.object(
                actions.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
        ):
            restart_result = await actions.restart_service.ainvoke(
                {"server_name": "demo", "service_name": "nginx.service"}
            )
            start_result = await actions.start_service.ainvoke(
                {"server_name": "demo", "service_name": "bt.service"}
            )
            stop_result = await actions.stop_service.ainvoke(
                {"server_name": "demo", "service_name": "bt.service"}
            )

        self.assertEqual(
            connection.commands,
            [
                "sudo -n -- /usr/local/sbin/aioms-service-control restart nginx.service",
                "sudo -n -- /usr/local/sbin/aioms-service-control start bt.service",
                "sudo -n -- /usr/local/sbin/aioms-service-control stop bt.service",
            ],
        )
        self.assertIn("当前状态：active", restart_result)
        self.assertIn("当前状态：active", start_result)
        self.assertIn("当前状态：inactive", stop_result)
