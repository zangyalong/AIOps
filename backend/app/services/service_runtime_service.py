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
_DOCKER_READ_WRAPPER = "/usr/local/sbin/aioms-docker-read"
_CHECK_TIMEOUT_SECONDS = 10


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
            ("stopped", "已停止")
            if exited.group(1) == "0"
            else ("error", "异常退出")
        )
    if lowered.startswith(("created", "paused", "removing")):
        return "stopped", "已停止"
    return "unknown", "状态未知"


def parse_docker_inventory(output: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for raw_line in output.splitlines():
        parts = raw_line.split("\t")
        if len(parts) >= 3 and parts[0].strip():
            rows[parts[0].strip()] = parts[2].strip()
    return rows


def _status(
    instance: ServiceInstance,
    state: RuntimeStatus,
    label: str,
    detail: str,
) -> ServiceInstanceStatusRead:
    return ServiceInstanceStatusRead(
        instance_id=instance.id,
        status=state,
        label=label,
        detail=detail,
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
            docker_inventory: dict[str, str] = {}
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
            systemd_outputs = await asyncio.gather(*systemd_checks)
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
        )

    for instance in docker_instances:
        name = instance.runtime_name or ""
        if not _SAFE_CONTAINER_NAME.fullmatch(name):
            results[instance.id] = _status(
                instance, "error", "检测失败", "Docker 容器名称格式不合法"
            )
        elif docker_error:
            results[instance.id] = _status(
                instance, "error", "检测失败", docker_error
            )
        elif name not in docker_inventory:
            results[instance.id] = _status(
                instance,
                "stopped",
                "已停止",
                f"Docker 容器 {name} 当前未运行",
            )
        else:
            raw_state = docker_inventory[name]
            state, label = classify_docker_state(raw_state)
            results[instance.id] = _status(
                instance, state, label, f"Docker/{name}：{raw_state}"
            )

    return list(results.values())


async def check_instance_statuses(
    instances: list[ServiceInstance], servers: dict[uuid.UUID, Server]
) -> list[ServiceInstanceStatusRead]:
    grouped: dict[uuid.UUID, list[ServiceInstance]] = defaultdict(list)
    result: list[ServiceInstanceStatusRead] = []
    for instance in instances:
        if instance.server_id is None:
            result.append(
                _status(instance, "unknown", "未绑定", "实例没有绑定服务器")
            )
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
