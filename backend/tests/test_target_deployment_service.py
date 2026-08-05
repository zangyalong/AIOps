from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models.server import Server
from app.schemas.server import DeploymentBundleSecrets, TargetDeploymentConfig
from app.services.target_deployment_service import (
    DeploymentBundleError,
    render_deploy_config,
    write_database_credentials,
)
from pydantic import SecretStr


def make_server() -> Server:
    return Server(
        name="web01",
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_user="aioms",
        deploy_ssh_user="aioms-deploy",
    )


class TargetDeploymentServiceTests(unittest.TestCase):
    def test_render_deploy_config_keeps_initial_alloy_map_empty(self) -> None:
        config = TargetDeploymentConfig(
            environment="prod",
            loki_push_url="http://monitor:3100/loki/api/v1/push",
            allow_docker_socket=True,
        )

        rendered = render_deploy_config(make_server(), config, redis_has_password=False)

        self.assertIn("SERVER_NAME=web01\n", rendered)
        self.assertIn("ALLOW_DOCKER_SOCKET=true\n", rendered)
        self.assertIn("ALLOY_SERVICE_MAP=\n", rendered)
        self.assertIn("START_ALLOY=false\n", rendered)
        self.assertIn("EXPORTER_IMAGE_MIRROR_PREFIX=m.daocloud.io\n", rendered)
        self.assertNotIn("database-secret-value", rendered)

    def test_enabled_database_exporter_requires_one_time_password(self) -> None:
        config = TargetDeploymentConfig(enable_mysql_exporter=True)

        with (
            tempfile.TemporaryDirectory() as temporary_dir,
            self.assertRaisesRegex(DeploymentBundleError, "MySQL exporter.*必须填写"),
        ):
            write_database_credentials(
                Path(temporary_dir), config, DeploymentBundleSecrets()
            )

    def test_credentials_are_written_as_private_separate_files(self) -> None:
        config = TargetDeploymentConfig(
            enable_mysql_exporter=True,
            enable_postgres_exporter=True,
            enable_redis_exporter=True,
        )
        secrets = DeploymentBundleSecrets(
            mysql_password=SecretStr('my"sql\\secret'),
            postgres_password=SecretStr("postgres-secret"),
            redis_password=SecretStr("redis-secret"),
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            redis_has_password = write_database_credentials(root, config, secrets)

            self.assertTrue(redis_has_password)
            mysql_config = root / "metrics/mysql-exporter.my.cnf"
            self.assertIn('password="my\\"sql\\\\secret"', mysql_config.read_text())
            self.assertIn("host=127.0.0.1\n", mysql_config.read_text())
            self.assertIn("port=3306\n", mysql_config.read_text())
            self.assertEqual(
                (root / "metrics/postgres-exporter.password").read_text(),
                "postgres-secret\n",
            )
            self.assertEqual(
                (root / "metrics/redis-exporter.password").read_text(),
                "redis-secret\n",
            )
            self.assertEqual(mysql_config.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
