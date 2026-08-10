"""受控服务启动、停止和重启执行器。"""

from __future__ import annotations

import re
import shlex
from typing import Literal

import asyncssh

from app.db.session import AsyncSessionLocal
from app.operations.schemas import OperationResult
from app.services import server_service, service_catalog_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

SERVICE_CONTROL_GATEWAY = "/usr/local/sbin/aioms-service-control"
SERVICE_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$")
DOCKER_CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ServiceAction = Literal["start", "stop", "restart"]
RuntimeType = Literal["systemd", "docker"]


async def control_service(
    action: ServiceAction,
    server_name: str,
    service_name: str,
    runtime_type: RuntimeType,
) -> OperationResult:
    service = service_name.strip()
    action_label = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
    if runtime_type == "systemd" and not SERVICE_UNIT_PATTERN.fullmatch(service):
        return OperationResult.failure(
            "systemd 服务名必须是明确的 .service 单元，已拒绝执行。",
            code="invalid_service_name",
        )
    if runtime_type == "docker" and not DOCKER_CONTAINER_PATTERN.fullmatch(service):
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
            runtime_type=runtime_type,
            runtime_name=service,
        )
        if instance is None:
            return OperationResult.failure(
                f'{server.name} 上的 {runtime_type} 服务 "{service}" '
                "尚未登记到服务目录，已拒绝执行。",
                code="service_not_registered",
            )
        if not instance.control_enabled:
            return OperationResult.failure(
                f'{server.name} 上的 {runtime_type} 服务 "{service}" '
                "未开启控制授权，已拒绝执行。"
                "请先在服务目录对应实例中开启控制授权。",
                code="control_not_enabled",
            )

    try:
        params = build_connect_params(server)
    except SSHNotConfigured:
        return OperationResult.failure(
            f"{server.name} 没有配置 AIOMS SSH 身份，无法{action_label}服务。"
            "请先配置 ssh_user / ssh_key_filename。",
            code="ssh_not_configured",
        )
    except SSHKeyMissing as exc:
        return OperationResult.failure(
            f"{exc}，请确认已经放到 {exc.key_path.parent} 目录下。",
            code="ssh_key_missing",
        )

    command = shlex.join(
        (
            "sudo",
            "-n",
            "--",
            SERVICE_CONTROL_GATEWAY,
            action,
            runtime_type,
            service,
        )
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
            controlled = await conn.run(command, check=False, timeout=20)
            if controlled.exit_status != 0:
                detail = f"{controlled.stdout or ''}{controlled.stderr or ''}".strip()
                return OperationResult.failure(
                    f"{action_label} {server.name} 上的 {service} 失败"
                    f"（exit={controlled.exit_status}）：{detail or '无输出'}",
                    code="gateway_failed",
                )
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return OperationResult.failure(
            f"连接或{action_label} {server.name} 上的 {service} 失败：{exc}",
            code="ssh_failed",
        )

    detail = f"{controlled.stdout or ''}{controlled.stderr or ''}".strip()
    return OperationResult.success(
        detail or f"已{action_label} {server.name} 上的服务 {service}。"
    )
