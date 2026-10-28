from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import stat
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
DOCKER_CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DATABASE_SECRET_MAX_LENGTH = 4096
BUNDLE_TIMEOUT_SECONDS = 60


class DeploymentBundleError(ValueError):
    pass


@dataclass(frozen=True)
class DeploymentBundleArtifact:
    archive_path: Path
    temporary_root: Path
    sha256: str
    key_filename: str


@dataclass(frozen=True)
class _SSHKeyFileBackup:
    content: bytes
    mode: int


@dataclass(frozen=True)
class _SSHKeyPairBackup:
    private_key: _SSHKeyFileBackup | None
    public_key: _SSHKeyFileBackup | None


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


def _normalize_service_control_allowlist(raw_allowlist: str) -> str:
    targets: list[str] = []
    for raw in raw_allowlist.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if entry.startswith(("systemd:", "docker:")):
            runtime_type, runtime_name = entry.split(":", 1)
        else:
            runtime_type, runtime_name = "systemd", entry
        valid = (
            runtime_type == "systemd" and SERVICE_UNIT_PATTERN.fullmatch(runtime_name)
        ) or (
            runtime_type == "docker"
            and DOCKER_CONTAINER_PATTERN.fullmatch(runtime_name)
        )
        if not valid:
            raise DeploymentBundleError(
                f"SSH_SERVICE_CONTROL_ALLOWLIST 中的运行时身份不合法：{entry}"
            )
        targets.append(f"{runtime_type}:{runtime_name}")
    return ",".join(sorted(set(targets)))


def _service_control_allowlist() -> str:
    return _normalize_service_control_allowlist(settings.service_control_allowlist)


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
    if (
        config.enable_redis_exporter
        and config.redis_use_password
        and redis_password is None
    ):
        raise DeploymentBundleError(
            "启用 Redis exporter 密码认证时必须填写本次打包密码"
        )
    return mysql_password, postgres_password, redis_password


def render_deploy_config(
    server: Server,
    config: TargetDeploymentConfig,
    *,
    redis_has_password: bool,
    service_control_allowlist: str | None = None,
) -> str:
    _validate_server_identity(server)
    allowlist = (
        _service_control_allowlist()
        if service_control_allowlist is None
        else _normalize_service_control_allowlist(service_control_allowlist)
    )

    return "\n".join(
        [
            "# 由 AIOps 服务器编辑页生成；数据库密码保存在同包的独立凭据文件中。",
            f"AIOPS_SSH_USER={server.ssh_user}",
            f"SERVICE_CONTROL_ALLOWLIST={allowlist}",
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
    if (
        not server.ssh_user
        or not SSH_USER_PATTERN.fullmatch(server.ssh_user)
        or server.ssh_user == "root"
    ):
        raise DeploymentBundleError("AIOps SSH 用户格式不合法且不能使用 root")


def _deployment_ssh_key_paths(server: Server) -> tuple[Path, Path]:
    _validate_server_identity(server)
    private_key_path = settings.ssh_key_dir / f"{server.name}-{server.ssh_user}"
    return private_key_path, Path(f"{private_key_path}.pub")


def _backup_ssh_key_file(path: Path) -> _SSHKeyFileBackup | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise DeploymentBundleError(f"SSH 密钥路径必须是普通文件：{path}")
    return _SSHKeyFileBackup(
        content=path.read_bytes(),
        mode=stat.S_IMODE(path.stat().st_mode),
    )


def remove_deployment_ssh_key_pair(server: Server) -> _SSHKeyPairBackup:
    """只删除当前服务器固定命名的密钥对，并保留失败回滚所需内容。"""
    private_key_path, public_key_path = _deployment_ssh_key_paths(server)
    backup = _SSHKeyPairBackup(
        private_key=_backup_ssh_key_file(private_key_path),
        public_key=_backup_ssh_key_file(public_key_path),
    )
    try:
        private_key_path.unlink(missing_ok=True)
        public_key_path.unlink(missing_ok=True)
    except Exception:
        restore_deployment_ssh_key_pair(server, backup)
        raise
    return backup


def restore_deployment_ssh_key_pair(server: Server, backup: _SSHKeyPairBackup) -> None:
    """删除本次可能生成的新密钥，并恢复打包前的原密钥对。"""
    private_key_path, public_key_path = _deployment_ssh_key_paths(server)
    for path, saved in (
        (private_key_path, backup.private_key),
        (public_key_path, backup.public_key),
    ):
        if path.exists() or path.is_symlink():
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                raise DeploymentBundleError(
                    f"无法安全恢复 SSH 密钥，路径不是文件：{path}"
                )
        if saved is not None:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(saved.content)
            path.chmod(saved.mode)


def cleanup_bundle(temporary_root: Path) -> None:
    shutil.rmtree(temporary_root, ignore_errors=True)


async def build_deployment_bundle(
    server: Server,
    secrets: DeploymentBundleSecrets,
    *,
    service_control_allowlist: str | None = None,
    regenerate_ssh_key_pair: bool = False,
) -> DeploymentBundleArtifact:
    _validate_server_identity(server)
    try:
        config = TargetDeploymentConfig.model_validate(server.deployment_config or {})
    except ValidationError as exc:
        raise DeploymentBundleError(f"服务器环境部署配置不合法：{exc}") from exc

    temporary_root = Path(tempfile.mkdtemp(prefix="aiops-target-bundle-"))
    assets_dir = temporary_root / "assets"
    output_dir = temporary_root / "output"
    config_path = temporary_root / "deploy.conf"
    key_pair_backup: _SSHKeyPairBackup | None = None

    try:
        assets_dir.mkdir(mode=0o700)
        redis_has_password = write_database_credentials(assets_dir, config, secrets)
        _write_private_file(
            config_path,
            render_deploy_config(
                server,
                config,
                redis_has_password=redis_has_password,
                service_control_allowlist=service_control_allowlist,
            ),
        )

        script_path = settings.target_server_scripts_dir / "make-bundle.sh"
        if not script_path.is_file():
            raise DeploymentBundleError(f"目标服务器打包脚本不存在：{script_path}")

        if regenerate_ssh_key_pair:
            key_pair_backup = remove_deployment_ssh_key_pair(server)

        try:
            # 仓库脚本可能以 0644 检出，显式使用 Bash，不依赖源文件执行位。
            process = await asyncio.create_subprocess_exec(
                "bash",
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
        except OSError as exc:
            raise DeploymentBundleError(
                "无法启动安装包生成程序，请检查 Bash 是否可用以及打包脚本的读取权限"
            ) from exc
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

        archive_path = output_dir / f"aiops-{server.name}.tar.gz"
        if not archive_path.is_file():
            raise DeploymentBundleError("打包脚本未生成预期的压缩包")

        sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        return DeploymentBundleArtifact(
            archive_path=archive_path,
            temporary_root=temporary_root,
            sha256=sha256,
            key_filename=f"{server.name}-{server.ssh_user}",
        )
    except Exception:
        if key_pair_backup is not None:
            restore_deployment_ssh_key_pair(server, key_pair_backup)
        cleanup_bundle(temporary_root)
        raise
