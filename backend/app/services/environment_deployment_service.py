from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncssh
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.schemas.server import DeploymentBundleSecrets, TargetDeploymentConfig
from app.services import service_catalog_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)
from app.services.target_deployment_service import (
    DeploymentBundleError,
    render_deploy_config,
)

_REMOTE_CONFIG = "/var/lib/aioms/incoming/deploy.conf"
_REMOTE_MANIFEST = "/var/lib/aioms/incoming/deployment.manifest"
_REMOTE_SECRET_PATHS = {
    "MYSQL_SECRET": "/var/lib/aioms/incoming/mysql-exporter.my.cnf",
    "POSTGRES_SECRET": "/var/lib/aioms/incoming/postgres-exporter.password",
    "REDIS_SECRET": "/var/lib/aioms/incoming/redis-exporter.password",
}
_APPLY_COMMAND = "sudo -n -- /usr/local/sbin/aioms-target-deploy apply-config"
_DEPLOY_TIMEOUT_SECONDS = 600
_SECRET_MAX_LENGTH = 4096


class EnvironmentDeploymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeploymentPayload:
    config: str
    manifest: str
    secret_files: dict[str, str]


def _secret_value(secret: SecretStr | None, label: str) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()
    if not value:
        return None
    if "\n" in value or "\r" in value or "\0" in value:
        raise EnvironmentDeploymentError(f"{label}不能包含换行或空字符")
    if len(value) > _SECRET_MAX_LENGTH:
        raise EnvironmentDeploymentError(f"{label}不能超过 {_SECRET_MAX_LENGTH} 个字符")
    return value


async def service_control_allowlist_for_server(db: AsyncSession, server: Server) -> str:
    instances = await service_catalog_service.list_instances(db, server_id=server.id)
    return ",".join(
        sorted(
            {
                f"{instance.runtime_type}:{instance.runtime_name}"
                for instance in instances
                if instance.control_enabled
                and instance.runtime_type in {"systemd", "docker"}
                and instance.runtime_name
            }
        )
    )


def build_deployment_payload(
    server: Server,
    config: TargetDeploymentConfig,
    secrets: DeploymentBundleSecrets,
    *,
    service_control_allowlist: str,
) -> DeploymentPayload:
    mysql_password = _secret_value(secrets.mysql_password, "MySQL 监控密码")
    postgres_password = _secret_value(secrets.postgres_password, "PostgreSQL 监控密码")
    redis_password = _secret_value(secrets.redis_password, "Redis 监控密码")

    actions: dict[str, str] = {}
    secret_files: dict[str, str] = {}

    if config.enable_mysql_exporter:
        actions["MYSQL_SECRET"] = "replace" if mysql_password else "preserve"
        if mysql_password:
            escaped = mysql_password.replace("\\", "\\\\").replace('"', '\\"')
            secret_files["MYSQL_SECRET"] = (
                "[client]\n"
                f"user={config.mysql_exporter_user}\n"
                f'password="{escaped}"\n'
                f"host={config.mysql_exporter_host}\n"
                f"port={config.mysql_exporter_port}\n"
            )
    else:
        actions["MYSQL_SECRET"] = "delete"

    if config.enable_postgres_exporter:
        actions["POSTGRES_SECRET"] = "replace" if postgres_password else "preserve"
        if postgres_password:
            secret_files["POSTGRES_SECRET"] = f"{postgres_password}\n"
    else:
        actions["POSTGRES_SECRET"] = "delete"

    redis_has_password = config.enable_redis_exporter and config.redis_use_password
    if redis_has_password:
        actions["REDIS_SECRET"] = "replace" if redis_password else "preserve"
        if redis_password:
            secret_files["REDIS_SECRET"] = f"{redis_password}\n"
    else:
        actions["REDIS_SECRET"] = "delete"

    manifest = "".join(
        f"{key}={actions[key]}\n"
        for key in ("MYSQL_SECRET", "POSTGRES_SECRET", "REDIS_SECRET")
    )
    return DeploymentPayload(
        config=render_deploy_config(
            server,
            config,
            redis_has_password=redis_has_password,
            service_control_allowlist=service_control_allowlist,
        ),
        manifest=manifest,
        secret_files=secret_files,
    )


async def _upload_text(sftp: Any, remote_path: str, content: str) -> None:
    temporary_path = f"{remote_path}.upload"
    async with sftp.open(temporary_path, "w") as remote_file:
        await remote_file.write(content)
    await sftp.chmod(temporary_path, 0o600)
    await sftp.posix_rename(temporary_path, remote_path)


async def apply_saved_environment(
    db: AsyncSession,
    server: Server,
    secrets: DeploymentBundleSecrets | None = None,
) -> str:
    try:
        config = TargetDeploymentConfig.model_validate(server.deployment_config or {})
    except ValidationError as exc:
        raise EnvironmentDeploymentError(f"服务器环境部署配置不合法：{exc}") from exc

    allowlist = await service_control_allowlist_for_server(db, server)
    try:
        payload = build_deployment_payload(
            server,
            config,
            secrets or DeploymentBundleSecrets(),
            service_control_allowlist=allowlist,
        )
    except DeploymentBundleError as exc:
        raise EnvironmentDeploymentError(str(exc)) from exc

    try:
        params = build_connect_params(server)
    except SSHNotConfigured as exc:
        raise EnvironmentDeploymentError(
            "服务器尚未配置 AIOMS SSH 用户和私钥，无法自动部署"
        ) from exc
    except SSHKeyMissing as exc:
        raise EnvironmentDeploymentError(str(exc)) from exc

    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                await _upload_text(sftp, _REMOTE_CONFIG, payload.config)
                for key, content in payload.secret_files.items():
                    await _upload_text(sftp, _REMOTE_SECRET_PATHS[key], content)
                await _upload_text(sftp, _REMOTE_MANIFEST, payload.manifest)
            result = await conn.run(
                _APPLY_COMMAND,
                check=False,
                timeout=_DEPLOY_TIMEOUT_SECONDS,
            )
    except TimeoutError as exc:
        raise EnvironmentDeploymentError(
            "自动部署超过 10 分钟，结果未知，请检查目标服务器"
        ) from exc
    except (asyncssh.Error, OSError) as exc:
        raise EnvironmentDeploymentError(f"连接、上传或自动部署失败：{exc}") from exc

    detail = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.exit_status != 0:
        raise EnvironmentDeploymentError(
            f"目标服务器自动部署失败（exit={result.exit_status}）：{detail or '无输出'}"
        )
    return detail or f"{server.name} 的环境配置已自动部署完成。"
