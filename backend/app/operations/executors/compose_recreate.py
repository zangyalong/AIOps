"""受控重建单个 Docker Compose 服务。"""

from __future__ import annotations

import re
import shlex

import asyncssh

from app.db.session import AsyncSessionLocal
from app.operations.schemas import OperationResult
from app.services import server_service, service_catalog_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

COMPOSE_RECREATE_GATEWAY = "/usr/local/sbin/aioms-compose-recreate"
DOCKER_CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


async def recreate_compose_service(
    server_name: str,
    container_name: str,
) -> OperationResult:
    """仅允许已登记、已开启控制授权的 Docker 容器进入固定网关。"""
    container = container_name.strip()
    if not DOCKER_CONTAINER_PATTERN.fullmatch(container):
        return OperationResult.failure(
            "Docker 运行对象必须是明确的容器名，已拒绝执行。",
            code="invalid_container_name",
        )

    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, server_name)
        if server is None:
            return OperationResult.failure(
                f"没有找到名称或 IP 为 {server_name} 的服务器，请先确认服务器。",
                code="server_not_found",
            )
        instance = await service_catalog_service.get_instance_by_runtime_identity(
            db,
            server_id=server.id,
            runtime_type="docker",
            runtime_name=container,
        )
        if instance is None:
            return OperationResult.failure(
                f'{server.name} 上的 Docker 容器 "{container}" '
                "尚未登记到服务目录，已拒绝执行。",
                code="service_not_registered",
            )
        if not instance.control_enabled:
            return OperationResult.failure(
                f'{server.name} 上的 Docker 容器 "{container}" '
                "未开启控制授权，已拒绝执行。"
                "请先在服务目录对应实例中开启控制授权。",
                code="control_not_enabled",
            )

    try:
        params = build_connect_params(server)
    except SSHNotConfigured:
        return OperationResult.failure(
            f"{server.name} 没有配置 AIOMS SSH 身份，无法重建 Compose 服务。",
            code="ssh_not_configured",
        )
    except SSHKeyMissing as exc:
        return OperationResult.failure(
            f"{exc}，请确认已经放到 {exc.key_path.parent} 目录下。",
            code="ssh_key_missing",
        )

    command = shlex.join(("sudo", "-n", "--", COMPOSE_RECREATE_GATEWAY, container))
    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            result = await conn.run(command, check=False, timeout=90)
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return OperationResult.failure(
            f"连接或重建 {server.name} 上的 Compose 服务失败：{exc}",
            code="ssh_failed",
        )

    detail = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.exit_status != 0:
        return OperationResult.failure(
            f"重建 {server.name} 上的 Compose 服务 {container} 失败"
            f"（exit={result.exit_status}）：{detail or '无输出'}",
            code="gateway_failed",
        )
    return OperationResult.success(
        detail or f"已重建 {server.name} 上的 Compose 服务 {container}。"
    )
