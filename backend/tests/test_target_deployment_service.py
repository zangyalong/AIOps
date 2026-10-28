from __future__ import annotations

import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from app.models.server import Server
from app.schemas.server import DeploymentBundleSecrets, TargetDeploymentConfig
from app.services.target_deployment_service import (
    DeploymentBundleError,
    _normalize_service_control_allowlist,
    build_deployment_bundle,
    cleanup_bundle,
    remove_deployment_ssh_key_pair,
    render_deploy_config,
    restore_deployment_ssh_key_pair,
    write_database_credentials,
)


def make_server() -> Server:
    return Server(
        name="web01",
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_user="aiops",
    )


class TargetDeploymentServiceTests(unittest.TestCase):
    def test_regenerating_key_pair_only_removes_current_server_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            key_dir = Path(temporary_dir)
            private_key = key_dir / "web01-aiops"
            public_key = key_dir / "web01-aiops.pub"
            unrelated_key = key_dir / "db01-aiops"
            private_key.write_text("old-private", encoding="utf-8")
            public_key.write_text("old-public", encoding="utf-8")
            unrelated_key.write_text("keep-me", encoding="utf-8")

            with patch(
                "app.services.target_deployment_service.settings.ssh_key_dir",
                key_dir,
            ):
                backup = remove_deployment_ssh_key_pair(make_server())

            self.assertFalse(private_key.exists())
            self.assertFalse(public_key.exists())
            self.assertEqual(unrelated_key.read_text(encoding="utf-8"), "keep-me")
            self.assertEqual(backup.private_key.content, b"old-private")
            self.assertEqual(backup.public_key.content, b"old-public")

    def test_failed_bundle_can_restore_original_key_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            key_dir = Path(temporary_dir)
            private_key = key_dir / "web01-aiops"
            public_key = key_dir / "web01-aiops.pub"
            private_key.write_text("old-private", encoding="utf-8")
            public_key.write_text("old-public", encoding="utf-8")
            private_key.chmod(0o600)
            public_key.chmod(0o644)

            with patch(
                "app.services.target_deployment_service.settings.ssh_key_dir",
                key_dir,
            ):
                backup = remove_deployment_ssh_key_pair(make_server())
                private_key.write_text("new-private", encoding="utf-8")
                public_key.write_text("new-public", encoding="utf-8")
                restore_deployment_ssh_key_pair(make_server(), backup)

            self.assertEqual(private_key.read_text(encoding="utf-8"), "old-private")
            self.assertEqual(public_key.read_text(encoding="utf-8"), "old-public")
            self.assertEqual(private_key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(public_key.stat().st_mode & 0o777, 0o644)

    def test_control_allowlist_normalizes_systemd_and_docker_targets(self) -> None:
        self.assertEqual(
            _normalize_service_control_allowlist(
                "nginx.service,docker:orders-api,systemd:mysqld.service"
            ),
            "docker:orders-api,systemd:mysqld.service,systemd:nginx.service",
        )

    def test_control_allowlist_rejects_unsafe_docker_name(self) -> None:
        with self.assertRaisesRegex(DeploymentBundleError, "运行时身份不合法"):
            _normalize_service_control_allowlist("docker:orders api")

    def test_render_deploy_config_keeps_initial_alloy_map_empty(self) -> None:
        config = TargetDeploymentConfig(
            environment="prod",
            loki_push_url="http://monitor:3100/loki/api/v1/push",
            allow_docker_socket=True,
        )

        with patch(
            "app.services.target_deployment_service.settings.ssh_service_control_allowlist",
            "example-app.service,nginx.service",
        ):
            rendered = render_deploy_config(
                make_server(), config, redis_has_password=False
            )

        self.assertIn("SERVER_NAME=web01\n", rendered)
        self.assertIn("AIOPS_SSH_USER=aiops\n", rendered)
        self.assertNotIn("AIOPS_DEPLOY_USER", rendered)
        self.assertIn("ALLOW_DOCKER_SOCKET=true\n", rendered)
        self.assertIn("ALLOY_SERVICE_MAP=\n", rendered)
        self.assertIn("START_ALLOY=false\n", rendered)
        self.assertIn("EXPORTER_IMAGE_MIRROR_PREFIX=m.daocloud.io\n", rendered)
        self.assertIn(
            "SERVICE_CONTROL_ALLOWLIST=systemd:example-app.service,systemd:nginx.service\n",
            rendered,
        )
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

    def test_password_protected_redis_requires_one_time_password(self) -> None:
        config = TargetDeploymentConfig(
            enable_redis_exporter=True,
            redis_use_password=True,
        )

        with (
            tempfile.TemporaryDirectory() as temporary_dir,
            self.assertRaisesRegex(DeploymentBundleError, "Redis exporter.*必须填写"),
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


class DeploymentBundleBuildTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(
        shutil.which("bash") and shutil.which("ssh-keygen"),
        "需要 Bash 和 ssh-keygen",
    )
    async def test_bundle_builds_from_non_executable_sources(self) -> None:
        source = Path(__file__).resolve().parents[2] / "scripts" / "target-server"
        server = make_server()
        server.deployment_config = TargetDeploymentConfig(
            enable_alloy=False,
            enable_node_exporter=False,
            enable_cadvisor=False,
        ).model_dump()
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            scripts = root / "scripts"
            shutil.copytree(
                source,
                scripts,
                ignore=shutil.ignore_patterns(
                    "dist",
                    "generated",
                    "__pycache__",
                    "deploy.conf",
                    "services.map",
                    "mysql-exporter.my.cnf",
                    "postgres-exporter.password",
                    "redis-exporter.password",
                ),
            )
            # 模拟跨平台检出时，所有仓库脚本均没有执行位。
            for path in scripts.rglob("*"):
                if path.is_file():
                    path.chmod(0o644)
            key_dir = root / "keys"
            with (
                patch(
                    "app.services.target_deployment_service.settings.target_server_scripts_dir",
                    scripts,
                ),
                patch(
                    "app.services.target_deployment_service.settings.ssh_key_dir",
                    key_dir,
                ),
            ):
                artifact = await build_deployment_bundle(
                    server, DeploymentBundleSecrets(), service_control_allowlist=""
                )
            try:
                with tarfile.open(artifact.archive_path) as archive:
                    names = archive.getnames()
                    script_count = 0
                    for member in archive.getmembers():
                        if not member.isfile():
                            continue
                        stream = archive.extractfile(member)
                        self.assertIsNotNone(stream)
                        with stream:
                            first_line = stream.readline()
                        if first_line.startswith(b"#!"):
                            script_count += 1
                            self.assertEqual(member.mode & 0o777, 0o755, member.name)
                    self.assertGreater(script_count, 20)
                    self.assertIn("aiops-web01/install.sh", names)
                    with archive.extractfile(
                        "aiops-web01/target-server/root-read/Dockerfile"
                    ) as stream:
                        dockerfile = stream.read().decode("utf-8")
                    self.assertIn("library/python:3.12-slim-bookworm", dockerfile)
                    self.assertIn(
                        'ENTRYPOINT ["/usr/local/bin/python3.12", ', dockerfile
                    )
                    config = archive.getmember("aiops-web01/target-server/deploy.conf")
                    self.assertEqual(config.mode & 0o777, 0o600)
                    self.assertNotIn("aiops-web01/role/web01-aiops", names)
                    self.assertNotIn("aiops-web01/role/aiops", names)
                    # 私钥字节不能进入安装包，公钥可以。
                    private_bytes = (key_dir / "web01-aiops").read_bytes()
                    for member in archive.getmembers():
                        if member.isfile():
                            with archive.extractfile(member) as stream:
                                self.assertNotIn(
                                    private_bytes, stream.read(), member.name
                                )
                self.assertEqual(
                    (scripts / "make-bundle.sh").stat().st_mode & 0o777, 0o644
                )
            finally:
                cleanup_bundle(artifact.temporary_root)

    async def test_process_start_failure_restores_keys_and_reports_bundle_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            key_dir = Path(temporary_dir)
            private_key = key_dir / "web01-aiops"
            public_key = key_dir / "web01-aiops.pub"
            private_key.write_text("original-private", encoding="utf-8")
            public_key.write_text("original-public", encoding="utf-8")
            private_key.chmod(0o600)
            with (
                patch(
                    "app.services.target_deployment_service.settings.ssh_key_dir",
                    key_dir,
                ),
                patch(
                    "app.services.target_deployment_service.asyncio.create_subprocess_exec",
                    new=AsyncMock(side_effect=PermissionError("permission denied")),
                ),
                self.assertRaisesRegex(DeploymentBundleError, "检查 Bash"),
            ):
                await build_deployment_bundle(
                    make_server(),
                    DeploymentBundleSecrets(),
                    regenerate_ssh_key_pair=True,
                )
            self.assertEqual(private_key.read_text(), "original-private")
            self.assertEqual(public_key.read_text(), "original-public")


if __name__ == "__main__":
    unittest.main()
