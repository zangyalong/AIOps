from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.graphs import chat_graph
from app.agents.tools import actions, catalog
from app.operations.executors import service_control


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
        action = command.split()[-3]
        runtime_type = command.split()[-2]
        service = command.split()[-1]
        state = (
            "inactive"
            if action == "stop" and runtime_type == "systemd"
            else "exited"
            if action == "stop"
            else "active"
            if runtime_type == "systemd"
            else "running"
        )
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
    def test_prompt_exposes_investigation_not_internal_executors(self) -> None:
        prompt = chat_graph.OPS_AGENT_PROMPT
        self.assertIn("调查与变更", prompt)
        self.assertIn("目标服务器真实的 `/`、PATH 和程序", prompt)
        self.assertIn("700/777", prompt)
        self.assertIn("不能据此推断", prompt)
        self.assertIn("本调查也始终不可写", prompt)
        self.assertIn("config-patch-preview", prompt)
        self.assertIn("apply_nginx_config", prompt)
        self.assertIn("Docker Compose", prompt)
        self.assertIn("普通 docker start/restart 不会应用新变量", prompt)
        self.assertNotIn("run_diagnostic_command", prompt)
        self.assertNotIn("run_root_read_command", prompt)
        self.assertNotIn("/host", prompt)
        self.assertNotIn("Supervisor", prompt)

    def test_catalog_has_disjoint_read_and_write_tools(self) -> None:
        """模型只看到统一调查入口；结构化变更仍逐项登记审批元数据。"""
        self.assertEqual(set(catalog.READ_TOOLS_BY_NAME), {"investigate"})
        self.assertNotIn("run_diagnostic_command", catalog.WRITE_TOOLS_BY_NAME)
        self.assertNotIn("run_root_read_command", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("restart_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("start_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("stop_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("create_alert_rule", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("register_server_services", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("apply_server_log_config", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("deploy_server_environment", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("update_server_file", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("patch_server_config", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("apply_nginx_config", catalog.WRITE_TOOLS_BY_NAME)
        self.assertFalse(
            set(catalog.READ_TOOLS_BY_NAME) & set(catalog.WRITE_TOOLS_BY_NAME)
        )
        read_spec = catalog.get_operation_spec("investigate")
        self.assertIsNotNone(read_spec)
        self.assertEqual(read_spec.effect, "read")
        self.assertEqual(read_spec.risk_level, "low")
        self.assertTrue(read_spec.audit_execution)
        restart_spec = catalog.get_operation_spec("restart_service")
        self.assertIsNotNone(restart_spec)
        self.assertEqual(restart_spec.effect, "write")
        self.assertEqual(restart_spec.risk_level, "high")
        patch_spec = catalog.get_operation_spec("patch_server_config")
        self.assertIsNotNone(patch_spec)
        self.assertEqual(patch_spec.risk_level, "medium")
        nginx_spec = catalog.get_operation_spec("apply_nginx_config")
        self.assertIsNotNone(nginx_spec)
        self.assertEqual(nginx_spec.risk_level, "high")
        self.assertEqual(
            {tool.name for tool in chat_graph.OPS_READ_TOOLS}, {"investigate"}
        )
        self.assertEqual(
            {tool.name for tool in chat_graph.OPS_WRITE_TOOLS},
            set(catalog.WRITE_TOOLS_BY_NAME),
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
        self.assertIn(
            "aioms-service-control restart systemd nginx.service",
            action["command"],
        )

        start_action = catalog.describe_write_action(
            {
                "id": "start-1",
                "name": "start_service",
                "args": {"server_name": "demo", "service_name": "bt.service"},
            }
        )
        self.assertEqual(start_action["risk_level"], "medium")

        stop_action = catalog.describe_write_action(
            {
                "id": "stop-1",
                "name": "stop_service",
                "args": {"server_name": "demo", "service_name": "bt.service"},
            }
        )
        self.assertEqual(stop_action["risk_level"], "high")
        self.assertEqual(stop_action["kind"], "service_stop")
        self.assertIn(
            "aioms-service-control stop systemd bt.service", stop_action["command"]
        )

        docker_stop_action = catalog.describe_write_action(
            {
                "id": "docker-stop-1",
                "name": "stop_service",
                "args": {
                    "server_name": "demo",
                    "service_name": "orders-api",
                    "runtime_type": "docker",
                },
            }
        )
        self.assertEqual(docker_stop_action["risk_level"], "high")
        self.assertEqual(docker_stop_action["runtime_type"], "docker")
        self.assertIn(
            "aioms-service-control stop docker orders-api",
            docker_stop_action["command"],
        )

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

        deployment_action = catalog.describe_write_action(
            {
                "id": "deploy-1",
                "name": "deploy_server_environment",
                "args": {"server_name": "web01"},
            }
        )
        self.assertEqual(deployment_action["risk_level"], "high")
        self.assertEqual(deployment_action["kind"], "server_environment_deploy")
        self.assertIn("aioms-target-deploy apply-config", deployment_action["command"])

        config_patch_action = catalog.describe_write_action(
            {
                "id": "config-patch-1",
                "name": "patch_server_config",
                "args": {
                    "server_name": "web01",
                    "path": "/sub2api-deploy/.env",
                    "config_format": "dotenv",
                    "selector": "SERVER_PORT",
                    "value": "8081",
                    "expected_sha256": "a" * 64,
                    "preview_hash": "b" * 64,
                    "preview_diff": "-SERVER_PORT=8080\n+SERVER_PORT=8081\n",
                },
            }
        )
        self.assertEqual(config_patch_action["risk_level"], "medium")
        self.assertEqual(config_patch_action["kind"], "server_config_patch")
        self.assertIn("SERVER_PORT=8081", config_patch_action["command"])

        nginx_action = catalog.describe_write_action(
            {
                "id": "nginx-apply-1",
                "name": "apply_nginx_config",
                "args": {
                    "server_name": "web01",
                    "path": "/www/server/panel/vhost/nginx/mingzang.online.conf",
                    "new_content": "server {}\n",
                    "expected_sha256": "a" * 64,
                    "preview_hash": "b" * 64,
                    "preview_diff": "-old\n+new\n",
                },
            }
        )
        self.assertEqual(nginx_action["risk_level"], "high")
        self.assertEqual(nginx_action["kind"], "nginx_config_apply")
        self.assertIn("失败自动恢复备份", nginx_action["command"])

    async def test_restart_service_rejects_unregistered_instance_before_ssh(
        self,
    ) -> None:
        """服务未登记时，即使审批层误放行，工具自身也不能连接 SSH。"""
        server = SimpleNamespace(id="server-id", name="demo", ip_address="127.0.0.1")
        with (
            patch.object(
                service_control, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                service_control.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control.service_catalog_service,
                "get_instance_by_runtime_identity",
                AsyncMock(return_value=None),
            ),
            patch.object(service_control.asyncssh, "connect") as connect,
        ):
            result = await actions.restart_service.ainvoke(
                {"server_name": "demo", "service_name": "nginx.service"}
            )

        self.assertFalse(result.ok)
        self.assertIn("尚未登记到服务目录", result.message)
        connect.assert_not_called()

    async def test_restart_service_rejects_disabled_control_authorization(
        self,
    ) -> None:
        server = SimpleNamespace(id="server-id", name="demo", ip_address="127.0.0.1")
        instance = SimpleNamespace(control_enabled=False)
        with (
            patch.object(
                service_control, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                service_control.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control.service_catalog_service,
                "get_instance_by_runtime_identity",
                AsyncMock(return_value=instance),
            ),
            patch.object(service_control.asyncssh, "connect") as connect,
        ):
            result = await actions.restart_service.ainvoke(
                {"server_name": "demo", "service_name": "nginx.service"}
            )

        self.assertFalse(result.ok)
        self.assertIn("未开启控制授权", result.message)
        connect.assert_not_called()

    async def test_service_control_executes_fixed_gateway_with_shared_aioms_identity(
        self,
    ) -> None:
        """三种动作都通过同一个非 root AIOMS 身份调用固定网关。"""
        server = SimpleNamespace(id="server-id", name="demo", ip_address="127.0.0.1")
        instance = SimpleNamespace(control_enabled=True)
        params = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            username="ops",
            client_keys=["test-key"],
        )
        connection = _FakeConnection()

        with (
            patch.object(
                service_control, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                service_control.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control.service_catalog_service,
                "get_instance_by_runtime_identity",
                AsyncMock(return_value=instance),
            ),
            patch.object(service_control, "build_connect_params", return_value=params),
            patch.object(
                service_control.asyncssh,
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
                "sudo -n -- /usr/local/sbin/aioms-service-control restart systemd nginx.service",
                "sudo -n -- /usr/local/sbin/aioms-service-control start systemd bt.service",
                "sudo -n -- /usr/local/sbin/aioms-service-control stop systemd bt.service",
            ],
        )
        self.assertTrue(restart_result.ok)
        self.assertTrue(start_result.ok)
        self.assertTrue(stop_result.ok)
        self.assertIn("当前状态：active", restart_result.message)
        self.assertIn("当前状态：active", start_result.message)
        self.assertIn("当前状态：inactive", stop_result.message)

    async def test_docker_control_reuses_service_tools_and_gateway(self) -> None:
        server = SimpleNamespace(id="server-id", name="demo", ip_address="127.0.0.1")
        instance = SimpleNamespace(control_enabled=True)
        connection = _FakeConnection()

        with (
            patch.object(
                service_control, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                service_control.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control.service_catalog_service,
                "get_instance_by_runtime_identity",
                AsyncMock(return_value=instance),
            ) as get_instance,
            patch.object(
                service_control,
                "build_connect_params",
                return_value=SimpleNamespace(
                    host="127.0.0.1",
                    port=22,
                    username="ops",
                    client_keys=["test-key"],
                ),
            ),
            patch.object(
                service_control.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
        ):
            result = await actions.stop_service.ainvoke(
                {
                    "server_name": "demo",
                    "service_name": "orders-api",
                    "runtime_type": "docker",
                }
            )

        self.assertEqual(get_instance.await_args.kwargs["runtime_type"], "docker")
        self.assertEqual(get_instance.await_args.kwargs["runtime_name"], "orders-api")
        self.assertEqual(
            connection.commands,
            ["sudo -n -- /usr/local/sbin/aioms-service-control stop docker orders-api"],
        )
        self.assertTrue(result.ok)
        self.assertIn("当前状态：exited", result.message)
