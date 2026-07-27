"""只读 SSH 诊断工具，通过 asyncssh 真实连接目标服务器执行。

对应 CLAUDE.md 安全红线里"SSH/Shell 工具要走白名单命令，不要开放任意执行"这条：不在
_ALLOWED_COMMANDS 里的命令一律拒绝，绝不会当成任意 shell 命令执行。

只有 servers 表里配置了 ssh_user + ssh_key_filename 的服务器才会真的发起连接；没配置的
服务器会明确提示"没有配置 SSH 连接信息"，不会假装执行或编造结果。私钥从仓库根目录的
ssh/ 读取（见 app/core/config.py 的 ssh_key_dir），密钥内容本身不进数据库、不进仓库。
"""

from __future__ import annotations

import re
import shlex

import asyncssh
from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.services import server_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

_COMMAND_TIMEOUT_SECONDS = 20
_MAX_OUTPUT_CHARS = 12_000
_SAFE_RESOURCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}")
_SAFE_CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_DOCKER_READ_WRAPPER = "/usr/local/sbin/aioms-docker-read"

# key 是模型可以提交的命令，value 是真正交给远端 shell 的规范化命令。systemctl 的几个短写
# 会被自动补成 --no-pager 和有限范围，避免分页器卡住或无界输出。
_EXACT_COMMANDS: dict[tuple[str, ...], tuple[str, ...]] = {
    # 主机与资源
    ("uptime",): ("uptime",),
    ("date",): ("date",),
    ("hostname",): ("hostname",),
    ("hostnamectl",): ("hostnamectl",),
    ("uname", "-a"): ("uname", "-a"),
    ("whoami",): ("whoami",),
    ("id",): ("id",),
    ("id", "-nG"): ("id", "-nG"),
    ("who",): ("who",),
    ("w",): ("w",),
    ("last", "-n", "20"): ("last", "-n", "20"),
    ("df", "-h"): ("df", "-h"),
    ("df", "-i"): ("df", "-i"),
    ("free", "-m"): ("free", "-m"),
    ("vmstat", "1", "5"): ("vmstat", "1", "5"),
    ("top", "-b", "-n", "1"): ("top", "-b", "-n", "1"),
    ("ps", "aux"): ("ps", "aux"),
    (
        "ps",
        "-eo",
        "pid,ppid,user,%cpu,%mem,stat,lstart,cmd",
        "--sort=-%cpu",
    ): (
        "ps",
        "-eo",
        "pid,ppid,user,%cpu,%mem,stat,lstart,cmd",
        "--sort=-%cpu",
    ),
    (
        "ps",
        "-eo",
        "pid,ppid,user,%cpu,%mem,stat,lstart,cmd",
        "--sort=-%mem",
    ): (
        "ps",
        "-eo",
        "pid,ppid,user,%cpu,%mem,stat,lstart,cmd",
        "--sort=-%mem",
    ),
    ("lsblk", "-f"): ("lsblk", "-f"),
    ("mount",): ("mount",),
    # 网络
    ("ip", "addr", "show"): ("ip", "addr", "show"),
    ("ip", "route", "show"): ("ip", "route", "show"),
    ("ss", "-lntup"): ("ss", "-lntup"),
    ("ss", "-s"): ("ss", "-s"),
    # systemd 与系统日志
    ("systemctl", "list-units"): (
        "systemctl",
        "list-units",
        "--type=service",
        "--all",
        "--no-pager",
        "--no-legend",
    ),
    ("systemctl", "list-units", "--type=service"): (
        "systemctl",
        "list-units",
        "--type=service",
        "--all",
        "--no-pager",
        "--no-legend",
    ),
    ("systemctl", "list-units", "--type=service", "--state=running"): (
        "systemctl",
        "list-units",
        "--type=service",
        "--state=running",
        "--no-pager",
        "--no-legend",
    ),
    (
        "systemctl",
        "list-units",
        "--type=service",
        "--state=running",
        "--no-pager",
        "--no-legend",
    ): (
        "systemctl",
        "list-units",
        "--type=service",
        "--state=running",
        "--no-pager",
        "--no-legend",
    ),
    ("systemctl", "--failed"): (
        "systemctl",
        "--failed",
        "--no-pager",
        "--no-legend",
    ),
    ("journalctl", "-p", "err", "-n", "100", "--no-pager"): (
        "journalctl",
        "-p",
        "err",
        "-n",
        "100",
        "--no-pager",
    ),
    # Docker：模型仍使用普通 docker 命令；真正执行时改写成 sudoers 精确授权的绝对路径。
    ("docker", "ps"): ("sudo", "-n", "--", "/usr/bin/docker", "ps"),
    ("docker", "ps", "-a"): (
        "sudo",
        "-n",
        "--",
        "/usr/bin/docker",
        "ps",
        "-a",
    ),
    ("docker", "images"): (
        "sudo",
        "-n",
        "--",
        "/usr/bin/docker",
        "images",
    ),
    ("docker", "stats", "--no-stream"): (
        "sudo",
        "-n",
        "--",
        "/usr/bin/docker",
        "stats",
        "--no-stream",
    ),
    ("docker", "system", "df"): (
        "sudo",
        "-n",
        "--",
        "/usr/bin/docker",
        "system",
        "df",
    ),
    ("docker", "version"): (
        "sudo",
        "-n",
        "--",
        "/usr/bin/docker",
        "version",
    ),
    ("docker", "network", "ls"): (
        "sudo",
        "-n",
        "--",
        "/usr/bin/docker",
        "network",
        "ls",
    ),
    ("docker", "volume", "ls"): (
        "sudo",
        "-n",
        "--",
        "/usr/bin/docker",
        "volume",
        "ls",
    ),
    ("crictl", "ps"): ("crictl", "ps"),
    ("crictl", "pods"): ("crictl", "pods"),
    # Kubernetes：只开放固定资源类型，不允许读取 Secret 或任意 -o 输出
    ("kubectl", "get", "nodes", "-o", "wide"): (
        "kubectl",
        "get",
        "nodes",
        "-o",
        "wide",
    ),
    ("kubectl", "get", "pods", "-A", "-o", "wide"): (
        "kubectl",
        "get",
        "pods",
        "-A",
        "-o",
        "wide",
    ),
    ("kubectl", "get", "deployments", "-A"): (
        "kubectl",
        "get",
        "deployments",
        "-A",
    ),
    ("kubectl", "get", "services", "-A"): (
        "kubectl",
        "get",
        "services",
        "-A",
    ),
    ("kubectl", "get", "events", "-A", "--sort-by=.lastTimestamp"): (
        "kubectl",
        "get",
        "events",
        "-A",
        "--sort-by=.lastTimestamp",
    ),
}

_COMMAND_HELP = (
    "主机资源：uptime / date / hostname / hostnamectl / uname -a / whoami / id / "
    "id -nG / who / w / last -n 20 / df -h / df -i / free -m / vmstat 1 5 / "
    "top -b -n 1 / ps aux / lsblk -f / mount；网络：ip addr show / "
    "ip route show / ss -lntup / ss -s；systemd：systemctl list-units / "
    "systemctl --failed / "
    "systemctl status <unit> / systemctl is-active <unit> / "
    "systemctl is-enabled <unit>；日志：journalctl -p err -n 100 --no-pager / "
    "journalctl -u <unit> -n 100 --no-pager；Docker：docker ps / docker ps -a / "
    "docker images / docker stats --no-stream / docker system df / docker version / "
    "docker network ls / docker volume ls / docker top <container> / "
    "docker logs --tail 100 <container>；容器与 K8s：crictl ps / crictl pods / "
    "kubectl get nodes -o wide / kubectl get pods -A -o wide / "
    "kubectl get deployments -A / kubectl get services -A / kubectl get events -A "
    "--sort-by=.lastTimestamp"
)


def _validated_command(command: str) -> str | None:
    """把允许的输入规范化成安全命令；任何额外 token、拼接符或重定向都会校验失败。"""
    try:
        tokens = tuple(shlex.split(command))
    except ValueError:
        return None
    exact = _EXACT_COMMANDS.get(tokens)
    if exact is not None:
        return shlex.join(exact)

    if (
        len(tokens) == 3
        and tokens[:2] == ("docker", "top")
        and _SAFE_CONTAINER_NAME.fullmatch(tokens[2])
    ):
        return shlex.join(
            ("sudo", "-n", "--", _DOCKER_READ_WRAPPER, "top", tokens[2])
        )

    if (
        len(tokens) == 5
        and tokens[:3] == ("docker", "logs", "--tail")
        and tokens[3] == "100"
        and _SAFE_CONTAINER_NAME.fullmatch(tokens[4])
    ):
        return shlex.join(
            ("sudo", "-n", "--", _DOCKER_READ_WRAPPER, "logs", tokens[4])
        )

    if (
        len(tokens) == 3
        and tokens[:2]
        in {
            ("systemctl", "status"),
            ("systemctl", "is-active"),
            ("systemctl", "is-enabled"),
        }
        and _SAFE_RESOURCE_NAME.fullmatch(tokens[2])
    ):
        if tokens[:2] == ("systemctl", "status"):
            return shlex.join((*tokens, "--no-pager"))
        return shlex.join(tokens)

    if (
        len(tokens) == 6
        and tokens[:2] == ("journalctl", "-u")
        and _SAFE_RESOURCE_NAME.fullmatch(tokens[2])
        and tokens[3:] == ("-n", "100", "--no-pager")
    ):
        return shlex.join(tokens)

    return None


@tool
async def run_diagnostic_command(server_name: str, command: str) -> str:
    """在指定服务器上通过 SSH 执行一条白名单内的只读诊断命令，返回真实执行结果。

    支持主机资源、进程、网络、systemd、journal、Docker、crictl 和 Kubernetes 的常用只读
    命令。systemctl status、journalctl -u 的资源名经过严格校验；不允许管道、重定向、
    命令拼接或任意额外参数。固定 Docker 查询会转换成 sudo -n + 绝对路径；带动态容器名的
    logs/top 会转换成服务器上的 aioms-docker-read 参数校验包装脚本。只有 servers 表里配置了
    SSH 连接信息的服务器才会连接执行。

    常用命令包括 whoami、id、id -nG、uptime、df -h、free -m、top -b -n 1、
    ps aux、ss -lntup、systemctl list-units、systemctl status <unit>、
    journalctl -u <unit> -n 100 --no-pager、docker ps、docker images、
    docker stats --no-stream、docker network ls、docker top <container>、
    docker logs --tail 100 <container>、crictl ps 和
    kubectl get pods -A -o wide。
    """
    safe_command = _validated_command(command)
    if safe_command is None:
        return f'命令 "{command}" 不在只读白名单内，已拒绝执行。当前允许：{_COMMAND_HELP}'

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
            result = await conn.run(
                safe_command, check=False, timeout=_COMMAND_TIMEOUT_SECONDS
            )
    except TimeoutError:
        return (
            f"执行 {server_name} 上的命令超过 {_COMMAND_TIMEOUT_SECONDS} 秒，已停止等待。"
        )
    except (asyncssh.Error, OSError) as exc:
        return f"连接或执行 {server_name} 失败：{exc}"

    output = f"{result.stdout or ''}{result.stderr or ''}".strip() or "（命令没有输出）"
    if len(output) > _MAX_OUTPUT_CHARS:
        output = output[:_MAX_OUTPUT_CHARS] + "\n…（输出过长，已截断）"
    return (
        f"（真实执行于 {server_name}，退出码 {result.exit_status}）"
        f" $ {safe_command}\n{output}"
    )
