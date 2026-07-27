from __future__ import annotations

import uuid
from collections import defaultdict

from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.models.service_catalog import Service, ServiceDependency
from app.services import server_service, service_catalog_service


@tool
async def query_server_services(server: str) -> str:
    """查询服务目录中绑定到指定服务器的部署实例。

    server 可以是 servers 表中的服务器名称或 IP。结果只代表 AIOMS 服务目录里的登记关系，
    不等同于实时扫描服务器进程或 systemd / Docker 运行状态。
    """
    identifier = server.strip()
    async with AsyncSessionLocal() as db:
        target = await server_service.get_server_by_name_or_ip(db, identifier)
        if target is None:
            return f'没有找到名称或 IP 为 "{identifier}" 的已纳管服务器。'

        instances = await service_catalog_service.list_instances(
            db, server_id=target.id
        )
        services = {
            item.id: item for item in await service_catalog_service.list_services(db)
        }

    lines: list[str] = []
    for instance in instances:
        service = services.get(instance.service_id)
        if service is None:
            continue
        lines.append(
            f"- {_label(service)}：实例={instance.name}，"
            f"metric_instance={instance.metric_instance or '未配置'}，"
            f"log_service={instance.log_service or '未配置'}"
        )

    if not lines:
        lines.append("- （没有登记绑定到这台服务器的部署实例）")
    return "\n".join(
        [
            f"## {target.name}（{target.ip_address}）上登记的服务",
            *lines,
            "说明：以上来自 AIOMS 服务目录，不代表实时进程或容器运行状态。",
        ]
    )


@tool
async def query_service_dependencies(service: str, environment: str = "") -> str:
    """查询一个服务直接依赖谁、谁直接依赖它，以及它有哪些部署实例。

    service 是服务目录中的名称；同名服务有多个环境时必须提供 environment。
    """
    async with AsyncSessionLocal() as db:
        try:
            target = await service_catalog_service.resolve_service(
                db, service.strip(), environment.strip() or None
            )
        except service_catalog_service.AmbiguousService as exc:
            return str(exc)
        if target is None:
            return _not_found(service, environment)
        services = {
            item.id: item for item in await service_catalog_service.list_services(db)
        }
        edges = await service_catalog_service.list_dependencies(db, target.id)
        instances = await service_catalog_service.list_instances(
            db, service_id=target.id
        )

    dependencies = [
        services[edge.downstream_service_id]
        for edge in edges
        if edge.upstream_service_id == target.id
        and edge.downstream_service_id in services
    ]
    dependents = [
        services[edge.upstream_service_id]
        for edge in edges
        if edge.downstream_service_id == target.id
        and edge.upstream_service_id in services
    ]
    instance_lines = [
        f"- {item.name}：metric_instance={item.metric_instance or '未配置'}，"
        f"log_service={item.log_service or '未配置'}"
        for item in instances
    ] or ["- （没有登记部署实例）"]
    return "\n".join(
        [
            f"## 服务依赖：{_label(target)}",
            "### 该服务直接依赖",
            *_service_lines(dependencies),
            "### 直接依赖该服务",
            *_service_lines(dependents),
            "### 部署实例",
            *instance_lines,
        ]
    )


@tool
async def query_impacted_services(
    service: str, environment: str = "", max_depth: int = 3
) -> str:
    """查询某服务故障后可能受影响的调用方，沿反向依赖关系向外遍历。

    max_depth 是最大依赖层数，允许 1 到 8；结果表示结构性影响可能，不代表已确认故障。
    """
    if max_depth < 1 or max_depth > 8:
        return "max_depth 必须在 1 到 8 之间。"
    graph = await _load_graph(service, environment)
    if isinstance(graph, str):
        return graph
    target, services, edges = graph
    depths = _walk(edges, target.id, direction="dependents", max_depth=max_depth)
    lines = _depth_lines(depths, services)
    return "\n".join(
        [
            f"## {_label(target)} 故障时的潜在影响范围",
            *(lines or ["- 没有找到依赖该服务的其他服务。"]),
            "说明：这里只按依赖拓扑推导可能受影响对象，是否实际异常还需结合告警、指标和日志确认。",
        ]
    )


@tool
async def query_upstream_candidates(
    service: str, environment: str = "", max_depth: int = 2
) -> str:
    """查询当前服务所依赖的下游组件，作为传播到当前服务的上游根因候选。

    max_depth 是最大依赖层数，允许 1 到 8；候选只表示需要继续调查，不能直接断言为根因。
    """
    if max_depth < 1 or max_depth > 8:
        return "max_depth 必须在 1 到 8 之间。"
    graph = await _load_graph(service, environment)
    if isinstance(graph, str):
        return graph
    target, services, edges = graph
    depths = _walk(edges, target.id, direction="dependencies", max_depth=max_depth)
    lines = _depth_lines(depths, services)
    return "\n".join(
        [
            f"## {_label(target)} 的依赖侧根因候选",
            *(lines or ["- 没有找到该服务依赖的其他服务。"]),
            "说明：这些对象只是依赖传播候选，必须继续核对它们同一时间窗内的真实证据。",
        ]
    )


async def _load_graph(
    service: str, environment: str
) -> tuple[Service, dict[uuid.UUID, Service], list[ServiceDependency]] | str:
    async with AsyncSessionLocal() as db:
        try:
            target = await service_catalog_service.resolve_service(
                db, service.strip(), environment.strip() or None
            )
        except service_catalog_service.AmbiguousService as exc:
            return str(exc)
        if target is None:
            return _not_found(service, environment)
        services = {
            item.id: item for item in await service_catalog_service.list_services(db)
        }
        edges = await service_catalog_service.list_dependencies(db)
    return target, services, edges


def _walk(
    edges: list[ServiceDependency],
    start: uuid.UUID,
    *,
    direction: str,
    max_depth: int,
) -> dict[uuid.UUID, int]:
    visited = {start}
    frontier = {start}
    depths: dict[uuid.UUID, int] = {}
    for depth in range(1, max_depth + 1):
        next_frontier: set[uuid.UUID] = set()
        for edge in edges:
            if direction == "dependencies" and edge.upstream_service_id in frontier:
                next_frontier.add(edge.downstream_service_id)
            if direction == "dependents" and edge.downstream_service_id in frontier:
                next_frontier.add(edge.upstream_service_id)
        next_frontier -= visited
        if not next_frontier:
            break
        for service_id in next_frontier:
            depths[service_id] = depth
        visited.update(next_frontier)
        frontier = next_frontier
    return depths


def _depth_lines(
    depths: dict[uuid.UUID, int], services: dict[uuid.UUID, Service]
) -> list[str]:
    grouped: dict[int, list[Service]] = defaultdict(list)
    for service_id, depth in depths.items():
        service = services.get(service_id)
        if service is not None:
            grouped[depth].append(service)
    lines: list[str] = []
    for depth in sorted(grouped):
        labels = "、".join(
            _label(item) for item in sorted(grouped[depth], key=lambda x: x.name)
        )
        lines.append(f"- 第 {depth} 层：{labels}")
    return lines


def _service_lines(services: list[Service]) -> list[str]:
    if not services:
        return ["- （无）"]
    return [f"- {_label(item)}" for item in sorted(services, key=lambda x: x.name)]


def _label(service: Service) -> str:
    owner = f"，负责人={service.owner}" if service.owner else ""
    return f"{service.name}/{service.environment}{owner}"


def _not_found(service: str, environment: str) -> str:
    suffix = f"/{environment.strip()}" if environment.strip() else ""
    return f"服务目录中没有找到 {service.strip()}{suffix}。"
