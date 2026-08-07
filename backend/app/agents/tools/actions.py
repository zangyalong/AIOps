"""有副作用的运维写工具。

本模块里的工具只能通过工具目录注册为写工具，并由 LangGraph 审批门调用。工具自身仍保留
服务白名单，形成“审批网关 + 执行参数校验”两层防线；不能因为已经有人点了批准就开放任意命令。
"""

from __future__ import annotations

import re
import shlex
from typing import Literal

import asyncssh
from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.services import server_service, service_catalog_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

SERVICE_CONTROL_GATEWAY = "/usr/local/sbin/aioms-service-control"
SERVICE_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$")
ServiceAction = Literal["start", "stop", "restart"]


async def _control_service(
    action: ServiceAction, server_name: str, service_name: str
) -> str:
    service = service_name.strip()
    action_label = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
    if not SERVICE_UNIT_PATTERN.fullmatch(service):
        return "服务名必须是明确的 systemd .service 单元，已拒绝执行。"

    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, server_name)
        if server is None:
            return f"没有找到名称或 IP 为 {server_name} 的服务器，请先确认服务器。"
        instance = await service_catalog_service.get_instance_by_runtime_identity(
            db,
            server_id=server.id,
            runtime_type="systemd",
            runtime_name=service,
        )
        if instance is None:
            return (
                f'{server.name} 上的服务 "{service}" 尚未登记到服务目录，已拒绝执行。'
            )
        if not instance.control_enabled:
            return (
                f'{server.name} 上的服务 "{service}" 未开启控制授权，已拒绝执行。'
                "请先在服务目录对应实例中开启控制授权。"
            )

    try:
        params = build_connect_params(server)
    except SSHNotConfigured:
        return (
            f"{server.name} 没有配置 AIOMS SSH 身份，无法{action_label}服务。"
            "请先配置 ssh_user / ssh_key_filename。"
        )
    except SSHKeyMissing as exc:
        return f"{exc}，请确认已经放到 {exc.key_path.parent} 目录下。"

    safe_service = shlex.quote(service)
    command = f"sudo -n -- {SERVICE_CONTROL_GATEWAY} {action} {safe_service}"
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
                return (
                    f"{action_label} {server.name} 上的 {service} 失败"
                    f"（exit={controlled.exit_status}）：{detail or '无输出'}"
                )
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return f"连接或{action_label} {server.name} 上的 {service} 失败：{exc}"

    detail = f"{controlled.stdout or ''}{controlled.stderr or ''}".strip()
    return detail or f"已{action_label} {server.name} 上的服务 {service}。"


@tool
async def start_service(server_name: str, service_name: str) -> str:
    """启动指定服务器上明确加入白名单的 systemd 服务。

    这是中风险写操作：必须先由用户在审批卡片中确认，并通过目标服务器固定写网关执行。
    """
    return await _control_service("start", server_name, service_name)


@tool
async def stop_service(server_name: str, service_name: str) -> str:
    """停止指定服务器上明确加入白名单的 systemd 服务。

    这是高风险写操作：必须经过审批卡片和详情弹窗两步确认，再通过固定写网关执行。
    """
    return await _control_service("stop", server_name, service_name)


@tool
async def restart_service(server_name: str, service_name: str) -> str:
    """重启指定服务器上明确加入白名单的 systemd 服务。

    这是高风险写操作：必须经过审批卡片和详情弹窗两步确认，再通过固定写网关执行。
    """
    return await _control_service("restart", server_name, service_name)
