"""受控服务启动、停止和重启执行器。"""

from __future__ import annotations

import re
import shlex
import uuid
from typing import Literal

import asyncssh

from app.db.session import AsyncSessionLocal
from app.operations.schemas import OperationCall, OperationContext, OperationResult
from app.services import server_service, service_catalog_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

SERVICE_CONTROL_GATEWAY = "/usr/local/sbin/aiops-service-control"
SERVICE_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$")
DOCKER_CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ServiceAction = Literal["start", "stop", "restart"]
RuntimeType = Literal["systemd", "docker"]


async def resolve_service_control_call(
    call: OperationCall,
    context: OperationContext,
) -> OperationCall:
    """审批前把模型的业务服务条件解析成唯一目录实例和真实运行时身份。"""
    del context
    args = call.normalized_args()
    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, args["server_name"])
        if server is None:
            raise ValueError(
                f"没有找到名称或 IP 为 {args['server_name']} 的已纳管服务器"
            )
        resolved = await service_catalog_service.resolve_control_target(
            db,
            server_id=server.id,
            service_name=args["service_name"],
            environment=args.get("environment") or "",
            instance_name=args.get("instance_name") or "",
        )

    instance = resolved.instance
    args.update(
        {
            "server_name": server.name,
            "service_name": resolved.service.name,
            "environment": resolved.service.environment,
            "instance_name": instance.name,
            "service_instance_id": str(instance.id),
            "runtime_type": instance.runtime_type,
            "runtime_name": instance.runtime_name,
        }
    )
    return OperationCall(id=call.id, name=call.name, args=args)


async def control_resolved_service(
    action: ServiceAction,
    server_name: str,
    service_instance_id: str,
    runtime_type: RuntimeType,
    runtime_name: str,
) -> OperationResult:
    service = runtime_name.strip()
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
        try:
            instance_id = uuid.UUID(service_instance_id)
        except ValueError:
            return OperationResult.failure(
                "冻结的服务实例标识无效，已拒绝执行；请重新发起操作。",
                code="invalid_service_instance_id",
            )
        instance = await service_catalog_service.get_instance(db, instance_id)
        if (
            instance is None
            or instance.server_id != server.id
            or instance.runtime_type != runtime_type
            or instance.runtime_name != service
        ):
            return OperationResult.failure(
                "服务目录实例或运行时身份在审批后发生变化，已拒绝执行；请重新发起操作。",
                code="service_target_changed",
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
            f"{server.name} 没有配置 AIOps SSH 身份，无法{action_label}服务。"
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
