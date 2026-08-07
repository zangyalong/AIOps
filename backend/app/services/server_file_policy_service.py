from __future__ import annotations

import re
import shlex
import uuid
from typing import Literal, cast

import asyncssh
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.models.service_catalog import ServiceFileAccess, ServiceInstance
from app.repositories import service_catalog_repository
from app.services import server_service, service_catalog_service
from app.services.ssh_service import SSHKeyMissing, SSHNotConfigured, build_connect_params

POLICY_GATEWAY = "/usr/local/sbin/aioms-file-policy"
PolicyAction = Literal["add", "remove"]
AccessLevel = Literal["read", "write"]
_POLICY_PATH_PATTERN = re.compile(r"^POLICY_PATH=(/.+)$", re.MULTILINE)


class ServerFilePolicySyncError(RuntimeError):
    pass


async def _apply_remote_policy(
    server: Server,
    *,
    action: PolicyAction,
    access_level: AccessLevel,
    path: str,
) -> str:
    try:
        params = build_connect_params(server)
    except SSHNotConfigured as exc:
        raise ServerFilePolicySyncError(
            "目标服务器没有配置 AIOMS SSH 身份，无法同步文件访问白名单"
        ) from exc
    except SSHKeyMissing as exc:
        raise ServerFilePolicySyncError(str(exc)) from exc

    command = (
        f"sudo -n -- {POLICY_GATEWAY} {action} {access_level} {shlex.quote(path)}"
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
        raise ServerFilePolicySyncError(
            f"连接目标服务器或同步文件访问白名单失败：{exc}"
        ) from exc

    detail = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.exit_status != 0:
        raise ServerFilePolicySyncError(
            f"目标服务器同步文件访问白名单失败（exit={result.exit_status}）："
            f"{detail or '无输出'}"
        )
    match = _POLICY_PATH_PATTERN.search(detail)
    if match is None:
        raise ServerFilePolicySyncError("目标服务器没有返回规范化后的授权路径")
    return match.group(1)


async def _instance_server(
    db: AsyncSession, instance: ServiceInstance
) -> Server:
    if instance.server_id is None:
        raise service_catalog_service.DirectoryNotFound("服务实例尚未绑定服务器")
    server = await server_service.get_server(db, instance.server_id)
    if server is None:
        raise service_catalog_service.DirectoryNotFound("server not found")
    return server


async def _other_server_access_exists(
    db: AsyncSession,
    *,
    server_id: uuid.UUID,
    access_level: str,
    path: str,
    excluding_access_id: uuid.UUID | None = None,
) -> bool:
    accesses = await service_catalog_repository.list_file_accesses(db)
    for current in accesses:
        if current.id == excluding_access_id:
            continue
        if current.access_level != access_level or current.path != path:
            continue
        instance = await service_catalog_service.get_instance(
            db, current.service_instance_id
        )
        if instance is not None and instance.server_id == server_id:
            return True
    return False


async def create_file_access(
    db: AsyncSession,
    instance: ServiceInstance,
    *,
    access_level: AccessLevel,
    path: str,
) -> ServiceFileAccess:
    server = await _instance_server(db, instance)
    canonical = await _apply_remote_policy(
        server,
        action="add",
        access_level=access_level,
        path=path,
    )
    existing = await service_catalog_repository.get_file_access_by_identity(
        db,
        service_instance_id=instance.id,
        access_level=access_level,
        path=canonical,
    )
    if existing is not None:
        return existing
    shared_remote_policy = await _other_server_access_exists(
        db,
        server_id=server.id,
        access_level=access_level,
        path=canonical,
    )
    try:
        return await service_catalog_repository.create_file_access(
            db,
            ServiceFileAccess(
                service_instance_id=instance.id,
                access_level=access_level,
                path=canonical,
            ),
        )
    except Exception as exc:
        if not shared_remote_policy:
            try:
                await _apply_remote_policy(
                    server,
                    action="remove",
                    access_level=access_level,
                    path=canonical,
                )
            except ServerFilePolicySyncError as rollback_exc:
                raise ServerFilePolicySyncError(
                    "数据库保存文件授权失败，且目标服务器白名单回滚失败："
                    f"{rollback_exc}"
                ) from exc
        raise


async def delete_file_access(
    db: AsyncSession,
    access: ServiceFileAccess,
) -> None:
    instance = await service_catalog_service.get_instance(
        db, access.service_instance_id
    )
    if instance is None:
        raise service_catalog_service.DirectoryNotFound("service instance not found")
    server = await _instance_server(db, instance)
    shared_remote_policy = await _other_server_access_exists(
        db,
        server_id=server.id,
        access_level=access.access_level,
        path=access.path,
        excluding_access_id=access.id,
    )
    if not shared_remote_policy:
        await _apply_remote_policy(
            server,
            action="remove",
            access_level=cast(AccessLevel, access.access_level),
            path=access.path,
        )
    await service_catalog_repository.delete_file_access(db, access)
