"""服务目录运行时状态检测。

状态检测只读取目标机信息：systemd 使用 ``is-active``，Docker 使用已安装的
只读网关。这里不提供启停能力，避免服务目录页面绕开 Agent 的人工审批门。
"""

from __future__ import annotations

import asyncio
import re
import shlex
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncssh

from app.models.server import Server
from app.models.service_catalog import ServiceInstance
from app.schemas.service_observability import RuntimeStatus, ServiceInstanceStatusRead
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

_SAFE_SYSTEMD_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,199}")
_SAFE_CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_LISTENER_PID = re.compile(r"pid=(\d+)")
_DOCKER_READ_WRAPPER = "/usr/local/sbin/aioms-docker-read"
_SERVICE_READ_WRAPPER = "/usr/local/sbin/aioms-service-read"
_CHECK_TIMEOUT_SECONDS = 10
_MAX_LISTENER_PIDS = 100


@dataclass(frozen=True)
class DockerRuntimeInfo:
    status: str
    ports: tuple[str, ...]


def classify_systemd_state(raw_state: str) -> tuple[RuntimeStatus, str]:
    state = raw_state.strip().lower()
    if state == "active":
        return "running", "运行中"
    if state in {"inactive", "deactivating"}:
        return "stopped", "已停止"
    if state in {"failed", "maintenance"}:
        return "error", "运行异常"
    if state == "activating":
        return "running", "启动中"
    return "unknown", "状态未知"


def classify_docker_state(raw_state: str) -> tuple[RuntimeStatus, str]:
    state = raw_state.strip()
    lowered = state.lower()
    if "unhealthy" in lowered or lowered.startswith(("restarting", "dead")):
        return "error", "运行异常"
    if lowered.startswith("up "):
        return "running", "运行中"
    exited = re.match(r"exited \((\d+)\)", lowered)
    if exited:
        return (
            ("stopped", "已停止") if exited.group(1) == "0" else ("error", "异常退出")
        )
    if lowered.startswith(("created", "paused", "removing")):
        return "stopped", "已停止"
    return "unknown", "状态未知"


def parse_docker_inventory(output: str) -> dict[str, DockerRuntimeInfo]:
    rows: dict[str, DockerRuntimeInfo] = {}
    for raw_line in output.splitlines():
        parts = raw_line.split("\t", maxsplit=3)
        if len(parts) >= 3 and parts[0].strip():
            ports = tuple(
                port
                for item in (parts[3].split(",") if len(parts) >= 4 else [])
                if (port := item.strip())
            )
            rows[parts[0].strip()] = DockerRuntimeInfo(
                status=parts[2].strip(),
                ports=ports,
            )
    return rows


def _parse_listener_endpoints(output: str) -> dict[int, set[str]]:
    listeners: dict[int, set[str]] = {}
    for raw_line in output.splitlines():
        columns = raw_line.strip().split(maxsplit=6)
        if len(columns) < 6 or columns[0].lower() == "netid":
            continue
        protocol = columns[0].lower()
        if protocol not in {"tcp", "udp"} or ":" not in columns[4]:
            continue
        port = columns[4].rsplit(":", 1)[1]
        if not port.isdigit():
            continue
        for raw_pid in _LISTENER_PID.findall(raw_line):
            listeners.setdefault(int(raw_pid), set()).add(f"{protocol}/{port}")
    return listeners


def _process_details_command(pids: list[int]) -> str:
    selected = [str(pid) for pid in sorted(set(pids))[:_MAX_LISTENER_PIDS]]
    return shlex.join(
        ("sudo", "-n", "--", _SERVICE_READ_WRAPPER, "processes", *selected)
    )


def _endpoint_sort_key(value: str) -> tuple[str, int]:
    protocol, port = value.split("/", 1)
    return protocol, int(port)


def _parse_systemd_ports(
    process_output: str,
    listeners: dict[int, set[str]],
    main_pids: dict[str, int] | None = None,
) -> dict[str, tuple[str, ...]]:
    ports_by_unit: dict[str, set[str]] = {}
    for raw_line in process_output.splitlines():
        columns = raw_line.strip().split(maxsplit=5)
        if len(columns) < 5 or not columns[0].isdigit():
            continue
        pid = int(columns[0])
        unit = columns[4]
        if not unit.endswith(".service") or pid not in listeners:
            continue
        ports_by_unit.setdefault(unit, set()).update(listeners[pid])
    for unit, pid in (main_pids or {}).items():
        if pid in listeners:
            ports_by_unit.setdefault(unit, set()).update(listeners[pid])
    return {
        unit: tuple(sorted(ports, key=_endpoint_sort_key))
        for unit, ports in ports_by_unit.items()
    }


def _parse_docker_top_pids(output: str) -> set[int]:
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if not lines or "PID" not in lines[0]:
        return set()
    pid_index = lines[0].index("PID")
    return {
        int(columns[pid_index])
        for columns in lines[1:]
        if len(columns) > pid_index and columns[pid_index].isdigit()
    }


def _status(
    instance: ServiceInstance,
    state: RuntimeStatus,
    label: str,
    detail: str,
    ports: tuple[str, ...] = (),
) -> ServiceInstanceStatusRead:
    return ServiceInstanceStatusRead(
        instance_id=instance.id,
        status=state,
        label=label,
        detail=detail,
        ports=list(ports),
        checked_at=datetime.now(UTC),
    )


def _same_status_for_all(
    instances: list[ServiceInstance],
    state: RuntimeStatus,
    label: str,
    detail: str,
) -> list[ServiceInstanceStatusRead]:
    return [_status(instance, state, label, detail) for instance in instances]


async def _check_server_instances(
    server: Server, instances: list[ServiceInstance]
) -> list[ServiceInstanceStatusRead]:
    results: dict[uuid.UUID, ServiceInstanceStatusRead] = {}
    actionable: list[ServiceInstance] = []
    for instance in instances:
        if not instance.runtime_type or not instance.runtime_name:
            results[instance.id] = _status(
                instance,
                "unknown",
                "未配置",
                "实例没有配置运行类型和运行对象",
            )
        elif instance.runtime_type == "process":
            results[instance.id] = _status(
                instance,
                "unknown",
                "无法检测",
                "未纳管进程没有稳定 PID，当前不能安全判断运行状态",
            )
        else:
            actionable.append(instance)

    if not actionable:
        return list(results.values())

    try:
        params = build_connect_params(server)
    except SSHNotConfigured:
        return [
            *results.values(),
            *_same_status_for_all(
                actionable,
                "error",
                "检测失败",
                f"{server.name} 未配置只读 SSH",
            ),
        ]
    except SSHKeyMissing as exc:
        return [
            *results.values(),
            *_same_status_for_all(actionable, "error", "检测失败", str(exc)),
        ]

    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            docker_instances = [
                item for item in actionable if item.runtime_type == "docker"
            ]
            docker_inventory: dict[str, DockerRuntimeInfo] = {}
            docker_error = ""
            if docker_instances:
                inventory_command = shlex.join(
                    ("sudo", "-n", "--", _DOCKER_READ_WRAPPER, "inventory-all")
                )
                inventory = await conn.run(
                    inventory_command, check=False, timeout=_CHECK_TIMEOUT_SECONDS
                )
                # 兼容尚未升级只读网关的目标机：旧版只有 inventory，
                # 而且只列运行中的容器。
                if inventory.exit_status != 0:
                    inventory = await conn.run(
                        shlex.join(
                            (
                                "sudo",
                                "-n",
                                "--",
                                _DOCKER_READ_WRAPPER,
                                "inventory",
                            )
                        ),
                        check=False,
                        timeout=_CHECK_TIMEOUT_SECONDS,
                    )
                if inventory.exit_status == 0:
                    docker_inventory = parse_docker_inventory(inventory.stdout or "")
                else:
                    docker_error = (
                        f"{inventory.stdout or ''}{inventory.stderr or ''}".strip()
                        or "Docker 只读网关执行失败"
                    )

            systemd_instances = [
                item for item in actionable if item.runtime_type == "systemd"
            ]
            systemd_checks = []
            systemd_main_pid_checks = []
            checked_systemd: list[ServiceInstance] = []
            for instance in systemd_instances:
                unit = instance.runtime_name or ""
                if not _SAFE_SYSTEMD_UNIT.fullmatch(unit):
                    results[instance.id] = _status(
                        instance,
                        "error",
                        "检测失败",
                        "systemd unit 名称格式不合法",
                    )
                    continue
                checked_systemd.append(instance)
                systemd_checks.append(
                    conn.run(
                        shlex.join(("systemctl", "is-active", "--", unit)),
                        check=False,
                        timeout=_CHECK_TIMEOUT_SECONDS,
                    )
                )
                systemd_main_pid_checks.append(
                    conn.run(
                        shlex.join(
                            (
                                "systemctl",
                                "show",
                                "--property=MainPID",
                                "--value",
                                "--",
                                unit,
                            )
                        ),
                        check=False,
                        timeout=_CHECK_TIMEOUT_SECONDS,
                    )
                )
            systemd_outputs = await asyncio.gather(*systemd_checks)
            systemd_main_pid_outputs = await asyncio.gather(*systemd_main_pid_checks)
            systemd_ports: dict[str, tuple[str, ...]] = {}
            docker_socket_ports: dict[str, tuple[str, ...]] = {}
            docker_without_published_ports = [
                instance
                for instance in docker_instances
                if (runtime_info := docker_inventory.get(instance.runtime_name or ""))
                and not runtime_info.ports
                and runtime_info.status.lower().startswith("up ")
                and _SAFE_CONTAINER_NAME.fullmatch(instance.runtime_name or "")
            ]
            if checked_systemd or docker_without_published_ports:
                socket_command = shlex.join(
                    ("sudo", "-n", "--", _SERVICE_READ_WRAPPER, "inet-sockets")
                )
                socket_output = await conn.run(
                    socket_command, check=False, timeout=_CHECK_TIMEOUT_SECONDS
                )
                if socket_output.exit_status == 0:
                    listeners = _parse_listener_endpoints(socket_output.stdout or "")
                    if listeners:
                        if checked_systemd:
                            process_output = await conn.run(
                                _process_details_command(list(listeners)),
                                check=False,
                                timeout=_CHECK_TIMEOUT_SECONDS,
                            )
                            main_pids = {
                                instance.runtime_name or "": int(output.stdout.strip())
                                for instance, output in zip(
                                    checked_systemd,
                                    systemd_main_pid_outputs,
                                    strict=True,
                                )
                                if output.exit_status == 0
                                and (output.stdout or "").strip().isdigit()
                                and int((output.stdout or "").strip()) > 0
                            }
                            if process_output.exit_status == 0:
                                systemd_ports = _parse_systemd_ports(
                                    process_output.stdout or "",
                                    listeners,
                                    main_pids,
                                )
                        for instance in docker_without_published_ports:
                            name = instance.runtime_name or ""
                            top_output = await conn.run(
                                shlex.join(
                                    (
                                        "sudo",
                                        "-n",
                                        "--",
                                        _DOCKER_READ_WRAPPER,
                                        "top",
                                        name,
                                    )
                                ),
                                check=False,
                                timeout=_CHECK_TIMEOUT_SECONDS,
                            )
                            if top_output.exit_status != 0:
                                continue
                            container_ports = {
                                endpoint
                                for pid in _parse_docker_top_pids(
                                    top_output.stdout or ""
                                )
                                for endpoint in listeners.get(pid, set())
                            }
                            if container_ports:
                                docker_socket_ports[name] = tuple(
                                    sorted(container_ports, key=_endpoint_sort_key)
                                )
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return [
            *results.values(),
            *_same_status_for_all(
                actionable,
                "error",
                "检测失败",
                f"连接或读取 {server.name} 失败：{exc}",
            ),
        ]

    for instance, output in zip(checked_systemd, systemd_outputs, strict=True):
        raw_state = f"{output.stdout or ''}{output.stderr or ''}".strip()
        state, label = classify_systemd_state(raw_state)
        results[instance.id] = _status(
            instance,
            state,
            label,
            f"systemd/{instance.runtime_name}：{raw_state or '无状态输出'}",
            systemd_ports.get(instance.runtime_name or "", ()),
        )

    for instance in docker_instances:
        name = instance.runtime_name or ""
        if not _SAFE_CONTAINER_NAME.fullmatch(name):
            results[instance.id] = _status(
                instance, "error", "检测失败", "Docker 容器名称格式不合法"
            )
        elif docker_error:
            results[instance.id] = _status(instance, "error", "检测失败", docker_error)
        elif name not in docker_inventory:
            results[instance.id] = _status(
                instance,
                "stopped",
                "已停止",
                f"Docker 容器 {name} 当前未运行",
            )
        else:
            runtime_info = docker_inventory[name]
            raw_state = runtime_info.status
            state, label = classify_docker_state(raw_state)
            results[instance.id] = _status(
                instance,
                state,
                label,
                f"Docker/{name}：{raw_state}",
                runtime_info.ports or docker_socket_ports.get(name, ()),
            )

    return list(results.values())


async def check_instance_statuses(
    instances: list[ServiceInstance], servers: dict[uuid.UUID, Server]
) -> list[ServiceInstanceStatusRead]:
    grouped: dict[uuid.UUID, list[ServiceInstance]] = defaultdict(list)
    result: list[ServiceInstanceStatusRead] = []
    for instance in instances:
        if instance.server_id is None:
            result.append(_status(instance, "unknown", "未绑定", "实例没有绑定服务器"))
        elif instance.server_id not in servers:
            result.append(
                _status(
                    instance,
                    "error",
                    "检测失败",
                    "关联的服务器记录不存在",
                )
            )
        else:
            grouped[instance.server_id].append(instance)

    checked = await asyncio.gather(
        *(
            _check_server_instances(servers[server_id], server_instances)
            for server_id, server_instances in grouped.items()
        )
    )
    for rows in checked:
        result.extend(rows)
    return result
