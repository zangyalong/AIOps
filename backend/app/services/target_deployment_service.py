from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr, ValidationError

from app.core.config import settings
from app.models.server import Server
from app.schemas.server import DeploymentBundleSecrets, TargetDeploymentConfig

SERVER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
SSH_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")
SERVICE_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$")
DATABASE_SECRET_MAX_LENGTH = 4096
BUNDLE_TIMEOUT_SECONDS = 60


class DeploymentBundleError(ValueError):
    pass


@dataclass(frozen=True)
class DeploymentBundleArtifact:
    archive_path: Path
    temporary_root: Path
    sha256: str
    readonly_key_filename: str
    deploy_key_filename: str


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _secret_value(secret: SecretStr | None, label: str) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()
    if not value:
        return None
    if "\n" in value or "\r" in value or "\0" in value:
        raise DeploymentBundleError(f"{label}不能包含换行或空字符")
    if len(value) > DATABASE_SECRET_MAX_LENGTH:
        raise DeploymentBundleError(
            f"{label}不能超过 {DATABASE_SECRET_MAX_LENGTH} 个字符"
        )
    return value


def _service_control_allowlist() -> str:
    services: list[str] = []
    for raw in settings.service_control_allowlist.split(","):
        service = raw.strip()
        if not service:
            continue
        if not SERVICE_UNIT_PATTERN.fullmatch(service):
            raise DeploymentBundleError(
                f"SSH_SERVICE_CONTROL_ALLOWLIST 中的服务名不合法：{service}"
            )
        services.append(service)
    return ",".join(sorted(set(services)))


def validate_bundle_secrets(
    config: TargetDeploymentConfig, secrets: DeploymentBundleSecrets
) -> tuple[str | None, str | None, str | None]:
    mysql_password = _secret_value(secrets.mysql_password, "MySQL 监控密码")
    postgres_password = _secret_value(secrets.postgres_password, "PostgreSQL 监控密码")
    redis_password = _secret_value(secrets.redis_password, "Redis 监控密码")

    if config.enable_mysql_exporter and mysql_password is None:
        raise DeploymentBundleError("启用 MySQL exporter 时必须填写本次打包密码")
    if config.enable_postgres_exporter and postgres_password is None:
        raise DeploymentBundleError("启用 PostgreSQL exporter 时必须填写本次打包密码")
    return mysql_password, postgres_password, redis_password


def render_deploy_config(
    server: Server, config: TargetDeploymentConfig, *, redis_has_password: bool
) -> str:
    if not server.ssh_user or not server.deploy_ssh_user:
        raise DeploymentBundleError("请先填写日常只读 SSH 用户和审批后部署 SSH 用户")

    return "\n".join(
        [
            "# 由 AIOMS 服务器编辑页生成；数据库密码保存在同包的独立凭据文件中。",
            f"AIOMS_SSH_USER={server.ssh_user}",
            f"AIOMS_DEPLOY_USER={server.deploy_ssh_user}",
            f"SERVICE_CONTROL_ALLOWLIST={_service_control_allowlist()}",
            f"ENABLE_ALLOY={_bool(config.enable_alloy)}",
            f"SERVER_NAME={server.name}",
            f"LOKI_PUSH_URL={config.loki_push_url}",
            "ALLOY_SERVICE_MAP=",
            f"ALLOW_DOCKER_SOCKET={_bool(config.allow_docker_socket)}",
            "START_ALLOY=false",
            f"ENVIRONMENT={config.environment}",
            "EXPORTER_IMAGE_MIRROR_PREFIX=m.daocloud.io",
            f"ENABLE_NODE_EXPORTER={_bool(config.enable_node_exporter)}",
            f"NODE_EXPORTER_IMAGE={config.node_exporter_image}",
            f"ENABLE_CADVISOR={_bool(config.enable_cadvisor)}",
            f"CADVISOR_IMAGE={config.cadvisor_image}",
            f"DOCKER_ROOT_DIR={config.docker_root_dir}",
            f"ENABLE_MYSQL_EXPORTER={_bool(config.enable_mysql_exporter)}",
            f"MYSQL_EXPORTER_IMAGE={config.mysql_exporter_image}",
            "MYSQL_EXPORTER_CONFIG=metrics/mysql-exporter.my.cnf",
            f"MYSQL_EXPORTER_NETWORK={config.mysql_exporter_network}",
            f"ENABLE_POSTGRES_EXPORTER={_bool(config.enable_postgres_exporter)}",
            f"POSTGRES_EXPORTER_IMAGE={config.postgres_exporter_image}",
            f"POSTGRES_EXPORTER_URI={config.postgres_exporter_uri}",
            f"POSTGRES_EXPORTER_USER={config.postgres_exporter_user}",
            "POSTGRES_EXPORTER_PASSWORD_FILE=metrics/postgres-exporter.password",
            f"POSTGRES_EXPORTER_NETWORK={config.postgres_exporter_network}",
            f"ENABLE_REDIS_EXPORTER={_bool(config.enable_redis_exporter)}",
            f"REDIS_EXPORTER_IMAGE={config.redis_exporter_image}",
            f"REDIS_EXPORTER_ADDRESS={config.redis_exporter_address}",
            f"REDIS_EXPORTER_USER={config.redis_exporter_user}",
            (
                "REDIS_EXPORTER_PASSWORD_FILE=metrics/redis-exporter.password"
                if redis_has_password
                else "REDIS_EXPORTER_PASSWORD_FILE="
            ),
            f"REDIS_EXPORTER_NETWORK={config.redis_exporter_network}",
            f"START_EXPORTERS={_bool(config.start_exporters)}",
            "",
        ]
    )


def _write_private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def write_database_credentials(
    assets_dir: Path,
    config: TargetDeploymentConfig,
    secrets: DeploymentBundleSecrets,
) -> bool:
    mysql_password, postgres_password, redis_password = validate_bundle_secrets(
        config, secrets
    )

    if config.enable_mysql_exporter and mysql_password is not None:
        escaped_password = mysql_password.replace("\\", "\\\\").replace('"', '\\"')
        _write_private_file(
            assets_dir / "metrics/mysql-exporter.my.cnf",
            "[client]\n"
            f"user={config.mysql_exporter_user}\n"
            f'password="{escaped_password}"\n'
            f"host={config.mysql_exporter_host}\n"
            f"port={config.mysql_exporter_port}\n",
        )
    if config.enable_postgres_exporter and postgres_password is not None:
        _write_private_file(
            assets_dir / "metrics/postgres-exporter.password",
            f"{postgres_password}\n",
        )
    if config.enable_redis_exporter and redis_password is not None:
        _write_private_file(
            assets_dir / "metrics/redis-exporter.password", f"{redis_password}\n"
        )
    return config.enable_redis_exporter and redis_password is not None


def _validate_server_identity(server: Server) -> None:
    if not SERVER_PATTERN.fullmatch(server.name):
        raise DeploymentBundleError("服务器名称只能包含字母、数字、点、下划线和短横线")
    if not server.ip_address or "\n" in server.ip_address or "\r" in server.ip_address:
        raise DeploymentBundleError("服务器地址不能为空或包含换行")
    if server.ssh_port is not None and not 1 <= server.ssh_port <= 65535:
        raise DeploymentBundleError("SSH 端口必须在 1 到 65535 之间")
    for label, user in (
        ("日常只读 SSH 用户", server.ssh_user),
        ("审批后部署 SSH 用户", server.deploy_ssh_user),
    ):
        if not user or not SSH_USER_PATTERN.fullmatch(user) or user == "root":
            raise DeploymentBundleError(f"{label}格式不合法且不能使用 root")
    if server.ssh_user == server.deploy_ssh_user:
        raise DeploymentBundleError("日常只读 SSH 用户和审批后部署 SSH 用户不能相同")


def cleanup_bundle(temporary_root: Path) -> None:
    shutil.rmtree(temporary_root, ignore_errors=True)


async def build_deployment_bundle(
    server: Server, secrets: DeploymentBundleSecrets
) -> DeploymentBundleArtifact:
    _validate_server_identity(server)
    try:
        config = TargetDeploymentConfig.model_validate(server.deployment_config or {})
    except ValidationError as exc:
        raise DeploymentBundleError(f"服务器环境部署配置不合法：{exc}") from exc

    temporary_root = Path(tempfile.mkdtemp(prefix="aioms-target-bundle-"))
    assets_dir = temporary_root / "assets"
    output_dir = temporary_root / "output"
    config_path = temporary_root / "deploy.conf"

    try:
        assets_dir.mkdir(mode=0o700)
        redis_has_password = write_database_credentials(assets_dir, config, secrets)
        _write_private_file(
            config_path,
            render_deploy_config(server, config, redis_has_password=redis_has_password),
        )

        script_path = settings.target_server_scripts_dir / "make-bundle.sh"
        if not script_path.is_file():
            raise DeploymentBundleError(f"目标服务器打包脚本不存在：{script_path}")

        process = await asyncio.create_subprocess_exec(
            str(script_path),
            "--server",
            server.name,
            "--host",
            server.ip_address,
            "--port",
            str(server.ssh_port or 22),
            "--config",
            str(config_path),
            "--key-dir",
            str(settings.ssh_key_dir),
            "--output-dir",
            str(output_dir),
            "--config-assets-dir",
            str(assets_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=BUNDLE_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise DeploymentBundleError("生成目标服务器安装包超时") from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = stdout.decode("utf-8", errors="replace").strip()
            raise DeploymentBundleError(detail or "目标服务器打包脚本执行失败")

        archive_path = output_dir / f"aioms-{server.name}.tar.gz"
        if not archive_path.is_file():
            raise DeploymentBundleError("打包脚本未生成预期的压缩包")

        sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        return DeploymentBundleArtifact(
            archive_path=archive_path,
            temporary_root=temporary_root,
            sha256=sha256,
            readonly_key_filename=f"{server.name}-{server.ssh_user}",
            deploy_key_filename=f"{server.name}-{server.deploy_ssh_user}",
        )
    except Exception:
        cleanup_bundle(temporary_root)
        raise
