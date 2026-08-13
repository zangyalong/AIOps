from __future__ import annotations

import json
import unittest
import uuid
from types import SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock, patch

from app.agents.tools import catalog, server_files
from app.operations.executors import server_file as server_file_executor
from app.operations.schemas import OperationCall, OperationContext, OperationResult
from app.services import server_file_policy_service


class _ConfigConnection:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def run(self, command: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.commands.append(command)
        return SimpleNamespace(
            exit_status=0,
            stdout="已原子应用结构化配置补丁\n",
            stderr="",
        )


class ServerFileToolValidationTest(unittest.IsolatedAsyncioTestCase):
    def test_snapshot_parser_preserves_exact_trailing_newline(self) -> None:
        output = (
            f"PATH=/etc/demo.conf\nSHA256={'a' * 64}\nREDACTED=false\n---\nport=3306\n"
        )
        snapshot = server_files._parse_snapshot(output)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.content, "port=3306\n")
        self.assertFalse(snapshot.redacted)

    def test_new_content_rejects_secrets_and_redaction_placeholder(self) -> None:
        self.assertIsNotNone(server_files._validate_new_content("password=hunter2\n"))
        self.assertIsNotNone(
            server_files._validate_new_content('{"password": "hunter2"}\n')
        )
        self.assertIsNotNone(
            server_files._validate_new_content("dsn=mysql://app:pw@db:3306/app\n")
        )
        self.assertIsNotNone(server_files._validate_new_content("token=[已脱敏]\n"))
        self.assertIsNone(server_files._validate_new_content("port=3306\n"))

    async def test_preview_refuses_redacted_source_file(self) -> None:
        server = SimpleNamespace(name="web-01")
        snapshot = server_files.RemoteFileSnapshot(
            path="/opt/app/.env",
            sha256="a" * 64,
            redacted=True,
            content="PASSWORD=[已脱敏]\nPORT=3306\n",
        )
        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_read_snapshot",
                AsyncMock(return_value=(snapshot, None)),
            ),
        ):
            result = await server_files.preview_server_file_update.ainvoke(
                {
                    "server_name": "web-01",
                    "path": "/opt/app/.env",
                    "new_content": "PORT=3307\n",
                }
            )
        self.assertIn("禁止整文件覆盖", result)
        self.assertIn("config-patch-preview", result)

    async def test_preview_returns_hash_and_exact_diff(self) -> None:
        server = SimpleNamespace(name="web-01")
        snapshot = server_files.RemoteFileSnapshot(
            path="/opt/app/app.conf",
            sha256="b" * 64,
            redacted=False,
            content="port=3306\n",
        )
        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_read_snapshot",
                AsyncMock(return_value=(snapshot, None)),
            ),
        ):
            result = await server_files.preview_server_file_update.ainvoke(
                {
                    "server_name": "web-01",
                    "path": "/opt/app/app.conf",
                    "new_content": "port=3307\n",
                }
            )
        payload = json.loads(result)
        self.assertEqual(payload["expected_sha256"], "b" * 64)
        self.assertIn("-port=3306", payload["preview_diff"])
        self.assertIn("+port=3307", payload["preview_diff"])
        self.assertFalse(payload["diff_truncated"])
        self.assertNotIn("write_tool", payload)

    async def test_broker_rebuilds_file_preview_from_actual_submitted_content(
        self,
    ) -> None:
        """末尾换行被模型改写时，审批前按实际内容重建哈希，不再陷入旧哈希重试。"""
        server = SimpleNamespace(name="web-01")
        snapshot = server_files.RemoteFileSnapshot(
            path="/opt/app/app.conf",
            sha256="a" * 64,
            redacted=False,
            content="port=3306\n",
        )
        preview_without_final_newline = server_files._build_preview(
            snapshot,
            "port=3307",
        )
        submitted_content = "port=3307\n"
        expected_preview = server_files._build_preview(snapshot, submitted_content)
        self.assertNotEqual(
            preview_without_final_newline["preview_hash"],
            expected_preview["preview_hash"],
        )

        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_read_snapshot",
                AsyncMock(return_value=(snapshot, None)),
            ),
        ):
            prepared = await catalog.OPERATION_BROKER.aprepare(
                OperationCall(
                    id="update-app-conf",
                    name="update_server_file",
                    args={
                        "server_name": "web-01",
                        "path": snapshot.path,
                        "new_content": submitted_content,
                    },
                ),
                OperationContext(
                    actor_id="operator-1",
                    actor_role="operator",
                    run_mode="interactive",
                ),
            )

        self.assertEqual(prepared.decision.result, "approval_required")
        self.assertEqual(prepared.call.args["expected_sha256"], snapshot.sha256)
        self.assertEqual(
            prepared.call.args["preview_hash"], expected_preview["preview_hash"]
        )
        self.assertEqual(
            prepared.call.args["preview_diff"], expected_preview["preview_diff"]
        )
        self.assertEqual(
            prepared.approval_view["command"], expected_preview["preview_diff"]
        )

    async def test_file_preview_resolver_rejects_unchanged_content_before_approval(
        self,
    ) -> None:
        server = SimpleNamespace(name="web-01")
        snapshot = server_files.RemoteFileSnapshot(
            path="/opt/app/app.conf",
            sha256="a" * 64,
            redacted=False,
            content="port=3306\n",
        )
        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_read_snapshot",
                AsyncMock(return_value=(snapshot, None)),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "不需要提交文件更新审批"):
                await server_files.resolve_update_server_file_call(
                    OperationCall(
                        id="no-change",
                        name="update_server_file",
                        args={
                            "server_name": "web-01",
                            "path": snapshot.path,
                            "new_content": snapshot.content,
                            "expected_sha256": snapshot.sha256,
                            "preview_hash": "b" * 64,
                            "preview_diff": "占位预览",
                        },
                    ),
                    OperationContext(
                        actor_id="operator-1",
                        actor_role="operator",
                        run_mode="interactive",
                    ),
                )

    async def test_structured_preview_works_without_reading_redacted_file(self) -> None:
        server = SimpleNamespace(name="web-01")
        gateway_payload = json.dumps(
            {
                "format": "dotenv",
                "selector": "SERVER_PORT",
                "source_sha256": "a" * 64,
                "output_sha256": "b" * 64,
                "before": "8080",
                "after": "8081",
                "line": 74,
                "changed": True,
            }
        )
        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_run_read_gateway",
                AsyncMock(return_value=(0, gateway_payload)),
            ) as run_gateway,
            patch.object(server_files, "_read_snapshot", AsyncMock()) as read_snapshot,
        ):
            result = await server_files.preview_server_config_patch.ainvoke(
                {
                    "server_name": "web-01",
                    "path": "/sub2api-deploy/.env",
                    "config_format": "dotenv",
                    "selector": "SERVER_PORT",
                    "value": "8081",
                }
            )

        payload = json.loads(result)
        self.assertEqual(payload["expected_sha256"], "a" * 64)
        self.assertIn("-SERVER_PORT=8080", payload["preview_diff"])
        self.assertIn("+SERVER_PORT=8081", payload["preview_diff"])
        self.assertIn("@@ -74,1 +74,1 @@", payload["preview_diff"])
        self.assertIn("密码和密钥不会返回", payload["notice"])
        read_snapshot.assert_not_awaited()
        run_gateway.assert_awaited_once_with(
            server,
            "config-preview",
            "/sub2api-deploy/.env",
            "dotenv",
            "SERVER_PORT",
            '"8081"',
        )

    async def test_yaml_preview_uses_pointer_without_reading_compose_file(
        self,
    ) -> None:
        server = SimpleNamespace(name="web-01")
        selector = "/services/sub2api/ports/0"
        before = "${SERVER_PORT:-8080}:8080"
        after = "${SERVER_PORT:-8081}:8080"
        gateway_payload = json.dumps(
            {
                "format": "yaml",
                "selector": selector,
                "source_sha256": "a" * 64,
                "output_sha256": "b" * 64,
                "before": before,
                "after": after,
                "line": 5,
                "changed": True,
            }
        )
        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_run_read_gateway",
                AsyncMock(return_value=(0, gateway_payload)),
            ) as run_gateway,
            patch.object(server_files, "_read_snapshot", AsyncMock()) as read_snapshot,
        ):
            result = await server_files.preview_server_config_patch.ainvoke(
                {
                    "server_name": "web-01",
                    "path": "/home/OnlineAPI/deploy/docker-compose.yml",
                    "config_format": "yaml",
                    "selector": selector,
                    "value": after,
                }
            )

        payload = json.loads(result)
        self.assertIn(f"@@ -5,1 +5,1 @@ yaml {selector}", payload["preview_diff"])
        self.assertIn(json.dumps(before), payload["preview_diff"])
        self.assertIn(json.dumps(after), payload["preview_diff"])
        read_snapshot.assert_not_awaited()
        run_gateway.assert_awaited_once_with(
            server,
            "config-preview",
            "/home/OnlineAPI/deploy/docker-compose.yml",
            "yaml",
            selector,
            json.dumps(after),
        )

    async def test_structured_patch_rechecks_preview_and_runs_fixed_gateway(
        self,
    ) -> None:
        server = SimpleNamespace(name="web-01")
        preview = server_files.ConfigPatchPreview(
            path="/sub2api-deploy/.env",
            config_format="dotenv",
            selector="SERVER_PORT",
            line=74,
            before="8080",
            after="8081",
            expected_sha256="a" * 64,
            output_sha256="b" * 64,
            preview_hash="c" * 64,
            preview_diff=(
                "@@ dotenv SERVER_PORT @@\n-SERVER_PORT=8080\n+SERVER_PORT=8081"
            ),
            changed=True,
        )
        connection = _ConfigConnection()
        params = SimpleNamespace(
            host="192.0.2.10",
            port=22,
            username="aioms",
            client_keys=["aioms-key"],
        )
        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_preview_config_patch",
                AsyncMock(return_value=(preview, None)),
            ),
            patch.object(
                server_files, "_connect_params_or_error", return_value=(params, None)
            ),
            patch.object(
                server_file_executor.asyncssh,
                "connect",
                return_value=connection,
            ),
        ):
            result = await server_files.patch_server_config.ainvoke(
                {
                    "server_name": "web-01",
                    "path": preview.path,
                    "config_format": "dotenv",
                    "selector": preview.selector,
                    "value": "8081",
                    "expected_sha256": preview.expected_sha256,
                    "preview_hash": preview.preview_hash,
                    "preview_diff": f"{preview.preview_diff}\n",
                }
            )

        self.assertTrue(result.ok)
        self.assertEqual(len(connection.commands), 1)
        self.assertIn("aioms-file-write config-apply", connection.commands[0])
        self.assertIn("SERVER_PORT", connection.commands[0])
        self.assertNotIn("POSTGRES_PASSWORD", connection.commands[0])

    async def test_structured_patch_reports_the_exact_changed_preview_field(
        self,
    ) -> None:
        server = SimpleNamespace(name="web-01")
        preview = server_files.ConfigPatchPreview(
            path="/home/OnlineAPI/deploy/docker-compose.yml",
            config_format="yaml",
            selector="/services/sub2api/ports/0",
            line=5,
            before="${SERVER_PORT:-8080}:8080",
            after="${SERVER_PORT:-8081}:8080",
            expected_sha256="a" * 64,
            output_sha256="b" * 64,
            preview_hash="c" * 64,
            preview_diff=(
                "@@ yaml /services/sub2api/ports/0 @@\n"
                '-"${SERVER_PORT:-8080}:8080"\n'
                '+"${SERVER_PORT:-8081}:8080"'
            ),
            changed=True,
        )
        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_preview_config_patch",
                AsyncMock(return_value=(preview, None)),
            ),
            patch.object(server_file_executor.asyncssh, "connect") as connect,
        ):
            result = await server_files.patch_server_config.ainvoke(
                {
                    "server_name": "web-01",
                    "path": preview.path,
                    "config_format": "yaml",
                    "selector": preview.selector,
                    "value": preview.after,
                    "expected_sha256": preview.expected_sha256,
                    "preview_hash": preview.preview_hash,
                    "preview_diff": preview.preview_diff.replace("8080", "8000", 1),
                }
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "preview_changed")
        self.assertIn("变化字段：preview_diff", result.message)
        self.assertEqual(result.metadata, {"changed_fields": ["preview_diff"]})
        connect.assert_not_called()

    async def test_nginx_path_uses_generic_file_editor_without_service_action(
        self,
    ) -> None:
        execute = AsyncMock(return_value=OperationResult.success("ok"))
        with patch.object(server_file_executor, "update_server_file", execute):
            result = await server_files.update_server_file.ainvoke(
                {
                    "server_name": "web-01",
                    "path": "/www/server/panel/vhost/nginx/mingzang.online.conf",
                    "new_content": "server {}\n",
                    "expected_sha256": "a" * 64,
                    "preview_hash": "b" * 64,
                    "preview_diff": "-old\n+new\n",
                }
            )

        self.assertTrue(result.ok)
        execute.assert_awaited_once()
        self.assertNotIn("gateway_action", execute.await_args.kwargs)

    async def test_structured_patch_rejects_sensitive_selector(self) -> None:
        result = await server_files.preview_server_config_patch.ainvoke(
            {
                "server_name": "web-01",
                "path": "/sub2api-deploy/.env",
                "config_format": "dotenv",
                "selector": "POSTGRES_PASSWORD",
                "value": "new-secret",
            }
        )

        self.assertIn("敏感字段", result)


class ServerFilePolicyReferenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_shared_server_policy_is_not_removed_until_last_reference(
        self,
    ) -> None:
        server_id = uuid.uuid4()
        current_id = uuid.uuid4()
        other_id = uuid.uuid4()
        current = SimpleNamespace(
            id=current_id,
            service_instance_id=uuid.uuid4(),
            access_level="read",
            path="/opt/shared",
        )
        other = SimpleNamespace(
            id=other_id,
            service_instance_id=uuid.uuid4(),
            access_level="read",
            path="/opt/shared",
        )
        instance = SimpleNamespace(server_id=server_id)
        server = SimpleNamespace(id=server_id)
        remote_policy = AsyncMock()
        delete_record = AsyncMock()
        with (
            patch.object(
                server_file_policy_service.service_catalog_service,
                "get_instance",
                AsyncMock(return_value=instance),
            ),
            patch.object(
                server_file_policy_service,
                "_instance_server",
                AsyncMock(return_value=server),
            ),
            patch.object(
                server_file_policy_service.service_catalog_repository,
                "list_file_accesses",
                AsyncMock(return_value=[current, other]),
            ),
            patch.object(
                server_file_policy_service,
                "_apply_remote_policy",
                remote_policy,
            ),
            patch.object(
                server_file_policy_service.service_catalog_repository,
                "delete_file_access",
                delete_record,
            ),
        ):
            await server_file_policy_service.delete_file_access(object(), current)

        remote_policy.assert_not_awaited()
        delete_record.assert_awaited_once()
