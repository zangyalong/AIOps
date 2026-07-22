"""有副作用的运维写工具。

本模块里的工具只能通过工具目录注册为写工具，并由 LangGraph 审批门调用。工具自身仍保留
服务白名单，形成“审批网关 + 执行参数校验”两层防线；不能因为已经有人点了批准就开放任意命令。
"""

from __future__ import annotations

import shlex

import asyncssh
from langchain_core.tools import tool

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services import server_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)


def restart_service_allowlist() -> frozenset[str]:
    """读取逗号分隔的服务白名单；默认空集合表示禁止所有服务重启。"""
    return frozenset(
        item.strip()
        for item in settings.ssh_restart_service_allowlist.split(",")
        if item.strip()
    )


@tool
async def restart_service(server_name: str, service_name: str) -> str:
    """在指定服务器上重启一个明确加入白名单的 systemd 服务。

    这是高风险写操作：必须先由用户在审批卡片中确认。服务还必须出现在环境变量
    SSH_RESTART_SERVICE_ALLOWLIST 中；默认白名单为空。工具不会接受任意 shell 命令。
    """
    service = service_name.strip()
    allowed = restart_service_allowlist()
    if not allowed:
        return (
            "服务重启白名单为空，已拒绝执行。请先配置 "
            "SSH_RESTART_SERVICE_ALLOWLIST，再重新发起请求。"
        )
    if service not in allowed:
        names = "、".join(sorted(allowed))
        return f'服务 "{service}" 不在重启白名单内，已拒绝执行。当前允许：{names}。'

    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, server_name)
    if server is None:
        return f"没有找到名称或 IP 为 {server_name} 的服务器，请先确认服务器。"

    try:
        params = build_connect_params(server)
    except SSHNotConfigured:
        return f"{server.name} 没有配置 SSH 连接信息，无法重启服务。"
    except SSHKeyMissing as exc:
        return f"{exc}，请确认已经放到 {exc.key_path.parent} 目录下。"

    safe_service = shlex.quote(service)
    restart_command = f"sudo -n -- systemctl restart -- {safe_service}"
    status_command = f"systemctl is-active -- {safe_service}"
    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            restarted = await conn.run(restart_command, check=False, timeout=20)
            if restarted.exit_status != 0:
                detail = f"{restarted.stdout or ''}{restarted.stderr or ''}".strip()
                return (
                    f"重启 {server.name} 上的 {service} 失败"
                    f"（exit={restarted.exit_status}）：{detail or '无输出'}"
                )
            status = await conn.run(status_command, check=False, timeout=10)
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return f"连接或重启 {server.name} 上的 {service} 失败：{exc}"

    state = f"{status.stdout or ''}{status.stderr or ''}".strip() or "状态未知"
    return f"已重启 {server.name} 上的服务 {service}；当前状态：{state}。"
