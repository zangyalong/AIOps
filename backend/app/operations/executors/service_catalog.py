"""把发现结果写入服务目录的执行器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from app.db.session import AsyncSessionLocal
from app.models.server import Server
from app.operations.schemas import OperationResult
from app.services import server_service, service_catalog_service
from app.services.service_catalog_service import DiscoveredServiceRegistration


class RuntimeCandidate(Protocol):
    runtime_type: str
    runtime_name: str
    image: str | None
    status: str | None
    ports: tuple[str, ...] | None
    user: str | None
    command: str | None


class DiscoveryResult(Protocol):
    candidates: Sequence[RuntimeCandidate]
    warnings: Sequence[str]


DiscoverRuntimeServices = Callable[[Server], Awaitable[DiscoveryResult]]
InstanceNameBuilder = Callable[[str, Any, str], str]


async def register_server_services(
    server_name: str,
    services: Sequence[Any],
    environment: str,
    *,
    discover_runtime_services: DiscoverRuntimeServices,
    build_instance_name: InstanceNameBuilder,
) -> OperationResult:
    identifier = server_name.strip()
    target_environment = environment.strip()
    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, identifier)
    if server is None:
        return OperationResult.failure(
            f'没有找到名称或 IP 为 "{identifier}" 的已纳管服务器。',
            code="server_not_found",
        )

    try:
        discovered = await discover_runtime_services(server)
    except ValueError as exc:
        return OperationResult.failure(str(exc), code="discovery_failed")

    actual = {
        (candidate.runtime_type, candidate.runtime_name): candidate
        for candidate in discovered.candidates
    }
    requested: list[DiscoveredServiceRegistration] = []
    missing: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in services:
        key = (item.runtime_type, item.runtime_name)
        if key in seen:
            continue
        seen.add(key)
        candidate = actual.get(key)
        if candidate is None:
            missing.append(f"{item.runtime_type}/{item.runtime_name}")
            continue
        description = f"由 AI 从 {server.name} 的 {item.runtime_type} 运行时发现并注册"
        if candidate.image:
            description += f"；镜像={candidate.image}"
        if candidate.status:
            description += f"；状态={candidate.status}"
        if candidate.ports:
            description += f"；监听端口={','.join(candidate.ports)}"
        if candidate.user:
            description += f"；运行用户={candidate.user}"
        if candidate.command:
            description += f"；启动命令={candidate.command}"
        requested.append(
            DiscoveredServiceRegistration(
                service_name=item.service_name,
                instance_name=build_instance_name(
                    server.name, item.runtime_type, item.runtime_name
                ),
                runtime_type=item.runtime_type,
                runtime_name=item.runtime_name,
                description=description,
            )
        )

    if not requested:
        suffix = f"；当前未发现：{'、'.join(missing)}" if missing else ""
        return OperationResult.failure(
            f"没有可注册的真实运行时服务，服务目录未修改{suffix}。",
            code="no_runtime_candidates",
        )

    async with AsyncSessionLocal() as db:
        result = await service_catalog_service.register_discovered_services(
            db,
            server=server,
            environment=target_environment,
            registrations=requested,
        )

    lines = [
        (
            f"已把 {server.name} 上的服务注册到 {target_environment} 环境："
            f"新增 {result.created_services} 个逻辑服务、"
            f"新增 {result.created_instances} 个部署实例、"
            f"跳过 {result.skipped_instances} 个已登记实例。"
        )
    ]
    if missing:
        lines.append(
            "以下审批候选在执行前复查时已不存在，未写入：" + "、".join(missing) + "。"
        )
    if discovered.warnings:
        lines.append("扫描提示：" + "；".join(discovered.warnings))
    return OperationResult.success("\n".join(lines))
