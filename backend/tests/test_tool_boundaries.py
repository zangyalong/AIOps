from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.graphs import chat_graph
from app.agents.prompts import (
    SIMPLIFIED_CHINESE_REQUIREMENT,
    DefaultServerContext,
    build_ops_system_prompt,
)
from app.agents.tools import actions, catalog, investigation
from app.operations.executors import service_control
from app.operations.schemas import OperationCall, OperationContext


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
    async def test_broker_does_not_inject_nested_investigation_defaults(self) -> None:
        call = OperationCall(
            id="servers-1",
            name="investigate",
            args={"area": "servers"},
        )
        normalized = catalog._normalize_tool_call(investigation.investigate, call)

        self.assertNotIn("parameters", normalized.args)

        query = AsyncMock(return_value="server list")
        with patch.object(
            investigation,
            "query_servers",
            SimpleNamespace(ainvoke=query),
        ):
            result = await catalog.OPERATION_BROKER.execute(
                call,
                OperationContext(
                    actor_id="test-worker",
                    actor_role="system",
                    run_mode="unattended",
                ),
            )

        self.assertTrue(result.ok)
        query.assert_awaited_once_with({})

    def test_explicit_investigation_parameters_are_preserved(self) -> None:
        call = OperationCall(
            id="metrics-1",
            name="investigate",
            args={
                "area": "metrics",
                "target": "web-01",
                "query": "memory",
                "parameters": {"target_type": "server"},
            },
        )

        normalized = catalog._normalize_tool_call(investigation.investigate, call)

        self.assertEqual(normalized.args["parameters"], {"target_type": "server"})

    def test_prompt_exposes_investigation_not_internal_executors(self) -> None:
        assembly = build_ops_system_prompt(
            tools=chat_graph._ops_tools(True),
            allow_write_tools=True,
            run_mode="interactive",
        )
        prompt = assembly.text
        self.assertIn("调查与变更", prompt)
        self.assertIn("目标服务器真实的 `/`、PATH 和程序", prompt)
        self.assertIn("700/777", prompt)
        self.assertIn("不能据此推断", prompt)
        self.assertIn("本调查也始终不可写", prompt)
        self.assertIn("config-patch-preview", prompt)
        self.assertIn("grant_server_file_write_access", prompt)
        self.assertIn("不得授权目录", prompt)
        self.assertIn("文件工具只修改文件", prompt)
        self.assertIn("完整文件写入只提交目标内容", prompt)
        self.assertIn("单独进入审批", prompt)
        self.assertNotIn("apply_nginx_config", prompt)
        self.assertIn("Docker Compose", prompt)
        self.assertIn("普通 docker start/restart 不会应用新变量", prompt)
        self.assertIn("recreate_compose_service", prompt)
        self.assertIn("同一项目还有 PostgreSQL、Redis", prompt)
        self.assertIn("<available_skills>", prompt)
        self.assertIn("read_skill", prompt)
        self.assertIn("docker-compose-operations", prompt)
        self.assertIn("configuration-changes", prompt)
        self.assertNotIn("# Docker Compose 排障", prompt)
        self.assertIn("同一事实只出现一次", prompt)
        self.assertIn("禁止出现", prompt)
        self.assertIn("禁止继续猜测字段", prompt)
        self.assertIn("严格照抄工具给出的正确调用结构重试一次", prompt)
        self.assertIn("不得根据常见端口猜测 exporter", prompt)
        self.assertIn("服务目录和 Prometheus 主机指标证据", prompt)
        self.assertIn("任何写工具返回失败", prompt)
        self.assertIn("禁止用“已改成”", prompt)
        self.assertIn("以写工具的结构化失败状态为准", prompt)
        self.assertNotIn("run_diagnostic_command", prompt)
        self.assertNotIn("run_root_read_command", prompt)
        self.assertNotIn("Supervisor", prompt)

    def test_prompt_injects_escaped_default_server_without_expanding_permissions(
        self,
    ) -> None:
        assembly = build_ops_system_prompt(
            tools=chat_graph._ops_tools(True),
            allow_write_tools=True,
            run_mode="interactive",
            default_server=DefaultServerContext(
                id="server-id",
                name='web-01"><unsafe',
                ip_address="139.196.175.226",
            ),
        )

        prompt = assembly.text
        self.assertIn('name="web-01&quot;&gt;&lt;unsafe"', prompt)
        self.assertIn("没有明确指定另一台服务器", prompt)
        self.assertIn("以用户明确指定的目标为准", prompt)
        self.assertIn("不扩大工具权限、文件授权、服务白名单或审批范围", prompt)
        self.assertNotIn('name="web-01"><unsafe"', prompt)

    def test_catalog_has_disjoint_read_and_write_tools(self) -> None:
        """模型只看到调查和 Skill 读取；结构化变更仍逐项登记审批元数据。"""
        self.assertEqual(set(catalog.READ_TOOLS_BY_NAME), {"investigate", "read_skill"})
        self.assertNotIn("run_diagnostic_command", catalog.WRITE_TOOLS_BY_NAME)
        self.assertNotIn("run_root_read_command", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("restart_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("start_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("stop_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("recreate_compose_service", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("create_alert_rule", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("register_server_services", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("apply_server_log_config", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("deploy_server_environment", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("grant_server_file_write_access", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("update_server_file", catalog.WRITE_TOOLS_BY_NAME)
        self.assertIn("patch_server_config", catalog.WRITE_TOOLS_BY_NAME)
        self.assertNotIn("apply_nginx_config", catalog.WRITE_TOOLS_BY_NAME)
        self.assertFalse(
            set(catalog.READ_TOOLS_BY_NAME) & set(catalog.WRITE_TOOLS_BY_NAME)
        )
        read_spec = catalog.get_operation_spec("investigate")
        self.assertIsNotNone(read_spec)
        self.assertEqual(read_spec.effect, "read")
        self.assertEqual(read_spec.risk_level, "low")
        self.assertTrue(read_spec.audit_execution)
        skill_spec = catalog.get_operation_spec("read_skill")
        self.assertIsNotNone(skill_spec)
        self.assertEqual(skill_spec.effect, "read")
        restart_spec = catalog.get_operation_spec("restart_service")
        self.assertIsNotNone(restart_spec)
        self.assertEqual(restart_spec.effect, "write")
        self.assertEqual(restart_spec.risk_level, "high")
        recreate_spec = catalog.get_operation_spec("recreate_compose_service")
        self.assertIsNotNone(recreate_spec)
        self.assertEqual(recreate_spec.risk_level, "high")
        patch_spec = catalog.get_operation_spec("patch_server_config")
        self.assertIsNotNone(patch_spec)
        self.assertEqual(patch_spec.risk_level, "medium")
        grant_spec = catalog.get_operation_spec("grant_server_file_write_access")
        self.assertIsNotNone(grant_spec)
        self.assertEqual(grant_spec.effect, "write")
        self.assertEqual(grant_spec.risk_level, "medium")
        self.assertEqual(
            {tool.name for tool in chat_graph.OPS_READ_TOOLS},
            {"investigate", "read_skill"},
        )
        self.assertEqual(
            {tool.name for tool in chat_graph.OPS_WRITE_TOOLS},
            set(catalog.WRITE_TOOLS_BY_NAME),
        )
        self.assertIn("必须使用简体中文", SIMPLIFIED_CHINESE_REQUIREMENT)

        service_schema = actions.restart_service.tool_call_schema.model_json_schema()
        self.assertEqual(
            set(service_schema["properties"]),
            {"server_name", "service_name", "environment", "instance_name"},
        )
        self.assertNotIn("runtime_type", service_schema["properties"])
        self.assertNotIn("runtime_name", service_schema["properties"])

        grant_action = catalog.describe_write_action(
            {
                "id": "grant-file-1",
                "name": "grant_server_file_write_access",
                "args": {
                    "server_name": "web-01",
                    "service_name": "sub2api",
                    "environment": "prod",
                    "instance_name": "sub2api",
                    "path": "/home/OnlineAPI/deploy/docker-compose.yml",
                },
            }
        )
        self.assertEqual(grant_action["risk_level"], "medium")
        self.assertEqual(grant_action["kind"], "server_file_write_access_grant")
        self.assertIn("精确文件写白名单", grant_action["command"])

        self.assertEqual(
            catalog.get_operation_spec("start_service").risk_level,
            "medium",
        )
        self.assertEqual(
            catalog.get_operation_spec("stop_service").risk_level,
            "high",
        )

        recreate_action = catalog.describe_write_action(
            {
                "id": "compose-recreate-1",
                "name": "recreate_compose_service",
                "args": {
                    "server_name": "demo",
                    "container_name": "sub2api",
                },
            }
        )
        self.assertEqual(recreate_action["risk_level"], "high")
        self.assertEqual(recreate_action["kind"], "compose_service_recreate")
        self.assertIn("aioms-compose-recreate sub2api", recreate_action["command"])

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

    def test_config_patch_is_normalized_before_approval_and_execution(self) -> None:
        raw_diff = "@@ yaml /services/app/ports/0 @@\n-8080\n+8081\n"
        call = OperationCall(
            id="yaml-port-patch",
            name="patch_server_config",
            args={
                "server_name": " web-01 ",
                "path": "/opt/app/docker-compose.yml",
                "config_format": "yaml",
                "selector": "/services/app/ports/0",
                "value": "8081:8080",
                "expected_sha256": "A" * 64,
                "preview_hash": "B" * 64,
                "preview_diff": raw_diff,
            },
        )

        prepared = catalog.OPERATION_BROKER.prepare(
            call,
            OperationContext(
                actor_id="admin-1",
                actor_role="admin",
                run_mode="interactive",
            ),
        )

        self.assertEqual(prepared.call.args["server_name"], "web-01")
        self.assertEqual(prepared.call.args["expected_sha256"], "a" * 64)
        self.assertEqual(prepared.call.args["preview_hash"], "b" * 64)
        self.assertEqual(prepared.call.args["preview_diff"], raw_diff.rstrip("\n"))
        self.assertEqual(
            prepared.approval_view["command"],
            raw_diff.rstrip("\n"),
        )

    async def test_service_control_is_resolved_before_approval(self) -> None:
        server_id = uuid.uuid4()
        instance_id = uuid.uuid4()
        server = SimpleNamespace(
            id=server_id,
            name="web-01",
            ip_address="127.0.0.1",
        )
        resolved = SimpleNamespace(
            service=SimpleNamespace(name="pixel-forge-frontend", environment="prod"),
            instance=SimpleNamespace(
                id=instance_id,
                name="pixel-forge-frontend",
                runtime_type="systemd",
                runtime_name="pixel-forge-frontend.service",
                control_enabled=True,
            ),
        )
        with (
            patch.object(
                service_control,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(
                service_control.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control.service_catalog_service,
                "resolve_control_target",
                AsyncMock(return_value=resolved),
            ),
        ):
            prepared = await catalog.OPERATION_BROKER.aprepare(
                OperationCall(
                    id="start-pixel-forge",
                    name="start_service",
                    args={
                        "server_name": "web-01",
                        "service_name": "pixel-forge-frontend",
                        "environment": "prod",
                    },
                ),
                OperationContext(
                    actor_id="admin-1",
                    actor_role="admin",
                    run_mode="interactive",
                ),
            )

        frozen = prepared.call.normalized_args()
        self.assertEqual(frozen["service_instance_id"], str(instance_id))
        self.assertEqual(frozen["runtime_type"], "systemd")
        self.assertEqual(frozen["runtime_name"], "pixel-forge-frontend.service")
        self.assertIn("pixel-forge-frontend.service", prepared.approval_view["title"])
        self.assertNotIn("service_instance_id", prepared.approval_view["args"])
        self.assertEqual(
            prepared.approval_view["args"]["service_name"],
            "pixel-forge-frontend",
        )

    async def test_restart_service_rejects_unresolved_direct_call_before_ssh(
        self,
    ) -> None:
        """绕过 Broker 的工具直调拿不到内部实例字段，必须失败关闭。"""
        with patch.object(service_control.asyncssh, "connect") as connect:
            result = await actions.restart_service.ainvoke(
                {"server_name": "demo", "service_name": "nginx"}
            )

        self.assertFalse(result.ok)
        self.assertIn("缺少审批前解析", result.message)
        connect.assert_not_called()

    async def test_service_control_rejects_runtime_changed_after_approval(self) -> None:
        server_id = uuid.uuid4()
        instance_id = uuid.uuid4()
        server = SimpleNamespace(id=server_id, name="demo", ip_address="127.0.0.1")
        changed = SimpleNamespace(
            id=instance_id,
            server_id=server_id,
            runtime_type="systemd",
            runtime_name="changed.service",
            control_enabled=True,
        )
        with (
            patch.object(
                service_control,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(
                service_control.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                service_control.service_catalog_service,
                "get_instance",
                AsyncMock(return_value=changed),
            ),
            patch.object(service_control.asyncssh, "connect") as connect,
        ):
            result = await actions.start_service.ainvoke(
                {
                    "server_name": "demo",
                    "service_name": "orders",
                    "service_instance_id": str(instance_id),
                    "runtime_type": "systemd",
                    "runtime_name": "orders.service",
                }
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "service_target_changed")
        connect.assert_not_called()

    async def test_service_control_executes_fixed_gateway_with_shared_aioms_identity(
        self,
    ) -> None:
        """三种动作都通过同一个非 root AIOMS 身份调用固定网关。"""
        server_id = uuid.uuid4()
        server = SimpleNamespace(id=server_id, name="demo", ip_address="127.0.0.1")
        instances = {
            "nginx.service": SimpleNamespace(
                id=uuid.uuid4(),
                server_id=server_id,
                runtime_type="systemd",
                runtime_name="nginx.service",
                control_enabled=True,
            ),
            "bt.service": SimpleNamespace(
                id=uuid.uuid4(),
                server_id=server_id,
                runtime_type="systemd",
                runtime_name="bt.service",
                control_enabled=True,
            ),
        }
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
                "get_instance",
                AsyncMock(
                    side_effect=lambda _db, instance_id: next(
                        item for item in instances.values() if item.id == instance_id
                    )
                ),
            ),
            patch.object(service_control, "build_connect_params", return_value=params),
            patch.object(
                service_control.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
        ):
            restart_result = await actions.restart_service.ainvoke(
                {
                    "server_name": "demo",
                    "service_name": "nginx",
                    "service_instance_id": str(instances["nginx.service"].id),
                    "runtime_type": "systemd",
                    "runtime_name": "nginx.service",
                }
            )
            start_result = await actions.start_service.ainvoke(
                {
                    "server_name": "demo",
                    "service_name": "bt",
                    "service_instance_id": str(instances["bt.service"].id),
                    "runtime_type": "systemd",
                    "runtime_name": "bt.service",
                }
            )
            stop_result = await actions.stop_service.ainvoke(
                {
                    "server_name": "demo",
                    "service_name": "bt",
                    "service_instance_id": str(instances["bt.service"].id),
                    "runtime_type": "systemd",
                    "runtime_name": "bt.service",
                }
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
        server_id = uuid.uuid4()
        server = SimpleNamespace(id=server_id, name="demo", ip_address="127.0.0.1")
        instance = SimpleNamespace(
            id=uuid.uuid4(),
            server_id=server_id,
            runtime_type="docker",
            runtime_name="orders-api",
            control_enabled=True,
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
                "get_instance",
                AsyncMock(return_value=instance),
            ),
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
                    "service_instance_id": str(instance.id),
                    "runtime_type": "docker",
                    "runtime_name": "orders-api",
                }
            )

        self.assertEqual(
            connection.commands,
            ["sudo -n -- /usr/local/sbin/aioms-service-control stop docker orders-api"],
        )
        self.assertTrue(result.ok)
        self.assertIn("当前状态：exited", result.message)
