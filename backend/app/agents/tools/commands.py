"""只读 SSH 诊断工具，通过 asyncssh 真实连接目标服务器执行。

对应 CLAUDE.md 安全红线里"SSH/Shell 工具要走白名单命令，不要开放任意执行"这条：不在
_ALLOWED_COMMANDS 里的命令一律拒绝，绝不会当成任意 shell 命令执行。

只有 servers 表里配置了 ssh_user + ssh_key_filename 的服务器才会真的发起连接；没配置的
服务器会明确提示"没有配置 SSH 连接信息"，不会假装执行或编造结果。私钥从仓库根目录的
ssh/ 读取（见 app/core/config.py 的 ssh_key_dir），密钥内容本身不进数据库、不进仓库。
"""

from __future__ import annotations

import asyncssh
from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.services import server_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

_ALLOWED_COMMANDS = frozenset({"uptime", "df -h", "free -m"})


@tool
async def run_diagnostic_command(server_name: str, command: str) -> str:
    """在指定服务器上通过 SSH 执行一条白名单内的只读诊断命令，返回真实执行结果。

    当前白名单：uptime / df -h / free -m。不在白名单内的命令一律拒绝。只有 servers 表
    里配置了 SSH 连接信息（ssh_user、ssh_key_filename）的服务器才能真正连接执行。
    """
    if command not in _ALLOWED_COMMANDS:
        allowed = "、".join(sorted(_ALLOWED_COMMANDS))
        return f'命令 "{command}" 不在白名单内，已拒绝执行。当前允许的命令：{allowed}'

    async with AsyncSessionLocal() as db:
        servers = await server_service.list_servers(db)
    server = next((s for s in servers if s.name == server_name), None)
    if server is None:
        return f"没有找到名为 {server_name} 的服务器，请先确认服务器名称。"

    try:
        params = build_connect_params(server)
    except SSHNotConfigured:
        return f"{server_name} 没有配置 SSH 连接信息（ssh_user / ssh_key_filename），无法执行命令。"
    except SSHKeyMissing as exc:
        return f"{exc}，请确认已经放到 {exc.key_path.parent} 目录下。"

    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            # 本地一次性测试沙箱，不校验 known_hosts；连接真实/生产服务器不能这样，
            # 要么提前 known_hosts 里加好指纹，要么显式传 known_hosts=<真实路径>。
            known_hosts=None,
        ) as conn:
            result = await conn.run(command, check=False)
    except (asyncssh.Error, OSError) as exc:
        return f"连接或执行 {server_name} 失败：{exc}"

    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    return f"（真实执行于 {server_name}）$ {command}\n{output}"
