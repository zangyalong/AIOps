from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import SecretStr

from app.models.server import Server
from app.schemas.server import DeploymentBundleSecrets, TargetDeploymentConfig
from app.services import environment_deployment_service


def make_server() -> Server:
    return Server(
        id=uuid.uuid4(),
        name="web01",
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_user="aioms",
        ssh_key_filename="web01-aioms",
    )


class EnvironmentDeploymentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_allowlist_only_contains_authorized_controllable_instances(
        self,
    ) -> None:
        server = make_server()
        db = object()
        instances = [
            SimpleNamespace(
                control_enabled=True,
                runtime_type="systemd",
                runtime_name="nginx.service",
            ),
            SimpleNamespace(
                control_enabled=True,
                runtime_type="systemd",
                runtime_name="bt.service",
            ),
            SimpleNamespace(
                control_enabled=True,
                runtime_type="docker",
                runtime_name="orders-api",
            ),
            SimpleNamespace(
                control_enabled=False,
                runtime_type="systemd",
                runtime_name="redis.service",
            ),
        ]

        with patch.object(
            environment_deployment_service.service_catalog_service,
            "list_instances",
            AsyncMock(return_value=instances),
        ) as list_instances:
            result = await environment_deployment_service.service_control_allowlist_for_server(
                db,
                server,  # type: ignore[arg-type]
            )

        self.assertEqual(
            result,
            "docker:orders-api,systemd:bt.service,systemd:nginx.service",
        )
        list_instances.assert_awaited_once_with(db, server_id=server.id)

    def test_payload_replaces_new_secret_and_preserves_existing_secret(self) -> None:
        server = make_server()
        config = TargetDeploymentConfig(
            enable_mysql_exporter=True,
            enable_redis_exporter=True,
            redis_use_password=True,
        )
        secrets = DeploymentBundleSecrets(mysql_password=SecretStr('my"sql\\secret'))

        payload = environment_deployment_service.build_deployment_payload(
            server,
            config,
            secrets,
            service_control_allowlist=("systemd:bt.service,systemd:mysqld.service"),
        )

        self.assertIn("MYSQL_SECRET=replace\n", payload.manifest)
        self.assertIn("POSTGRES_SECRET=delete\n", payload.manifest)
        self.assertIn("REDIS_SECRET=preserve\n", payload.manifest)
        self.assertIn(
            "SERVICE_CONTROL_ALLOWLIST=systemd:bt.service,systemd:mysqld.service\n",
            payload.config,
        )
        self.assertIn(
            'password="my\\"sql\\\\secret"',
            payload.secret_files["MYSQL_SECRET"],
        )
        self.assertNotIn("REDIS_SECRET", payload.secret_files)

    def test_payload_rejects_secret_with_newline(self) -> None:
        with self.assertRaisesRegex(
            environment_deployment_service.EnvironmentDeploymentError,
            "不能包含换行",
        ):
            environment_deployment_service.build_deployment_payload(
                make_server(),
                TargetDeploymentConfig(enable_mysql_exporter=True),
                DeploymentBundleSecrets(mysql_password=SecretStr("unsafe\npassword")),
                service_control_allowlist="",
            )

    async def test_apply_uploads_fixed_files_and_runs_fixed_gateway(self) -> None:
        server = make_server()
        server.deployment_config = TargetDeploymentConfig().model_dump()
        connection = MagicMock()
        connection.run = AsyncMock(
            return_value=SimpleNamespace(
                exit_status=0,
                stdout="部署完成\n",
                stderr="",
            )
        )
        sftp_context = MagicMock()
        sftp_context.__aenter__.return_value = object()
        connection.start_sftp_client.return_value = sftp_context
        connection_context = MagicMock()
        connection_context.__aenter__.return_value = connection

        with (
            patch.object(
                environment_deployment_service,
                "service_control_allowlist_for_server",
                AsyncMock(return_value="bt.service"),
            ),
            patch.object(
                environment_deployment_service,
                "build_connect_params",
                return_value=SimpleNamespace(
                    host="192.0.2.10",
                    port=22,
                    username="aioms",
                    client_keys=["/tmp/test-key"],
                ),
            ),
            patch.object(
                environment_deployment_service.asyncssh,
                "connect",
                return_value=connection_context,
            ),
            patch.object(
                environment_deployment_service,
                "_upload_text",
                AsyncMock(),
            ) as upload,
        ):
            detail = await environment_deployment_service.apply_saved_environment(
                object(),
                server,  # type: ignore[arg-type]
            )

        self.assertEqual(detail, "部署完成")
        self.assertEqual(upload.await_count, 2)
        self.assertEqual(
            [call.args[1] for call in upload.await_args_list],
            [
                "/var/lib/aioms/incoming/deploy.conf",
                "/var/lib/aioms/incoming/deployment.manifest",
            ],
        )
        connection.run.assert_awaited_once_with(
            "sudo -n -- /usr/local/sbin/aioms-target-deploy apply-config",
            check=False,
            timeout=600,
        )


if __name__ == "__main__":
    unittest.main()
