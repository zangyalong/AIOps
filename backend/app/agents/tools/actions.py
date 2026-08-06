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

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services import server_service
from app.services.ssh_service import (
    SSHDeployNotConfigured,
    SSHKeyMissing,
    build_deploy_connect_params,
)

SERVICE_CONTROL_GATEWAY = "/usr/local/sbin/aioms-service-control"
SERVICE_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$")
ServiceAction = Literal["start", "stop", "restart"]


def service_control_allowlist() -> frozenset[str]:
    """读取逗号分隔的服务控制白名单；默认空集合表示拒绝全部启停和重启。"""
    return frozenset(
        item.strip()
        for item in settings.service_control_allowlist.split(",")
        if item.strip()
    )


def restart_service_allowlist() -> frozenset[str]:
    """兼容旧调用名称；三种服务控制动作现在共用同一白名单。"""
    return service_control_allowlist()


async def _control_service(
    action: ServiceAction, server_name: str, service_name: str
) -> str:
    service = service_name.strip()
    allowed = service_control_allowlist()
    action_label = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
    if not SERVICE_UNIT_PATTERN.fullmatch(service):
        return "服务名必须是明确的 systemd .service 单元，已拒绝执行。"
    if not allowed:
        return (
            "服务控制白名单为空，已拒绝执行。请先配置 "
            "SSH_SERVICE_CONTROL_ALLOWLIST，再重新发起请求。"
        )
    if service not in allowed:
        names = "、".join(sorted(allowed))
        return f'服务 "{service}" 不在控制白名单内，已拒绝执行。当前允许：{names}。'

    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, server_name)
    if server is None:
        return f"没有找到名称或 IP 为 {server_name} 的服务器，请先确认服务器。"

    try:
        params = build_deploy_connect_params(server)
    except SSHDeployNotConfigured:
        return (
            f"{server.name} 没有配置审批后部署 SSH 身份，无法{action_label}服务。"
            "请先配置 deploy_ssh_user / deploy_ssh_key_filename。"
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

    这是高风险写操作：必须先由用户在审批卡片中确认，并通过目标服务器固定写网关执行。
    """
    return await _control_service("start", server_name, service_name)


@tool
async def stop_service(server_name: str, service_name: str) -> str:
    """停止指定服务器上明确加入白名单的 systemd 服务。

    这是高风险写操作：必须先由用户在审批卡片中确认，并通过目标服务器固定写网关执行。
    """
    return await _control_service("stop", server_name, service_name)


@tool
async def restart_service(server_name: str, service_name: str) -> str:
    """重启指定服务器上明确加入白名单的 systemd 服务。

    这是高风险写操作：必须先由用户在审批卡片中确认，并通过目标服务器固定写网关执行。
    """
    return await _control_service("restart", server_name, service_name)
