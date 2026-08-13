from __future__ import annotations

import shlex
from typing import Literal

import asyncssh
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.models.service_catalog import ServiceInstance
from app.services import server_service, service_catalog_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

POLICY_GATEWAY = "/usr/local/sbin/aiops-service-policy"
PolicyAction = Literal["add", "remove"]


class ServiceControlPolicySyncError(RuntimeError):
    pass


async def _apply_remote_policy(
    server: Server,
    *,
    action: PolicyAction,
    runtime_type: str,
    runtime_name: str,
) -> str:
    try:
        params = build_connect_params(server)
    except SSHNotConfigured as exc:
        raise ServiceControlPolicySyncError(
            "目标服务器没有配置 AIOps SSH 身份，无法同步服务控制白名单"
        ) from exc
    except SSHKeyMissing as exc:
        raise ServiceControlPolicySyncError(str(exc)) from exc

    command = (
        f"sudo -n -- {POLICY_GATEWAY} {action} "
        f"{shlex.quote(runtime_type)} {shlex.quote(runtime_name)}"
    )
    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            result = await conn.run(command, check=False, timeout=20)
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        raise ServiceControlPolicySyncError(
            f"连接目标服务器或同步服务控制白名单失败：{exc}"
        ) from exc

    detail = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.exit_status != 0:
        raise ServiceControlPolicySyncError(
            f"目标服务器同步服务控制白名单失败（exit={result.exit_status}）："
            f"{detail or '无输出'}"
        )
    return detail


async def update_control_authorization(
    db: AsyncSession,
    instance: ServiceInstance,
    *,
    enabled: bool,
) -> ServiceInstance:
    """先同步目标机 root 白名单，再提交实例授权；数据库失败时尽力回滚远端。"""
    service_catalog_service.validate_control_target(instance)
    assert instance.server_id is not None
    assert instance.runtime_type is not None
    assert instance.runtime_name is not None

    server = await server_service.get_server(db, instance.server_id)
    if server is None:
        raise service_catalog_service.DirectoryNotFound("server not found")

    action: PolicyAction = "add" if enabled else "remove"
    await _apply_remote_policy(
        server,
        action=action,
        runtime_type=instance.runtime_type,
        runtime_name=instance.runtime_name,
    )

    previous = instance.control_enabled
    try:
        return await service_catalog_service.set_control_authorization(
            db, instance, enabled=enabled
        )
    except Exception as exc:
        instance.control_enabled = previous
        if previous != enabled:
            rollback_action: PolicyAction = "add" if previous else "remove"
            try:
                await _apply_remote_policy(
                    server,
                    action=rollback_action,
                    runtime_type=instance.runtime_type,
                    runtime_name=instance.runtime_name,
                )
            except ServiceControlPolicySyncError as rollback_exc:
                raise ServiceControlPolicySyncError(
                    "数据库保存授权状态失败，且目标服务器白名单回滚失败："
                    f"{rollback_exc}"
                ) from exc
        raise
