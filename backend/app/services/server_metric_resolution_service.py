"""根据服务目录与 Prometheus 证据解析服务器的主机指标实例。"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.db.session import AsyncSessionLocal
from app.models.server import Server
from app.models.service_catalog import Service, ServiceInstance
from app.services import service_catalog_service
from app.services.observability_service import prom_query


def _normalized_identity(*values: str | None) -> str:
    return "-".join(
        part
        for value in values
        if value
        for part in [re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")]
        if part
    )


def _catalog_host_metric_instances(
    instances: Iterable[ServiceInstance],
    services: Iterable[Service],
) -> tuple[str, ...]:
    """从服务目录中找明确登记为 node-exporter 的 metric_instance。"""
    services_by_id = {service.id: service for service in services}
    candidates: set[str] = set()
    for instance in instances:
        service = services_by_id.get(instance.service_id)
        identity = _normalized_identity(
            service.name if service else None,
            instance.name,
            instance.runtime_name,
        )
        metric_instance = str(instance.metric_instance or "").strip()
        if "node-exporter" in identity and metric_instance:
            candidates.add(metric_instance)
    return tuple(sorted(candidates))


def _prometheus_host_metric_instances(
    ip_address: str,
    rows: Iterable[dict],
) -> tuple[str, ...]:
    """从 node_uname_info 查询结果中筛出该服务器真实暴露主机指标的实例。"""
    ip = ip_address.strip()
    if not ip:
        return ()
    candidates = {
        str((row.get("metric") or {}).get("instance") or "").strip()
        for row in rows
        if isinstance(row, dict)
    }
    return tuple(
        sorted(
            instance
            for instance in candidates
            if instance and (instance == ip or instance.startswith(f"{ip}:"))
        )
    )


async def resolve_server_host_metric_instance(
    server: Server,
) -> tuple[str | None, str | None]:
    """按显式映射、服务目录、Prometheus 主机指标的顺序确定 instance。"""
    configured = str((server.monitor_labels or {}).get("instance") or "").strip()
    if configured:
        return configured, None

    async with AsyncSessionLocal() as db:
        instances = await service_catalog_service.list_instances(
            db, server_id=server.id
        )
        services = await service_catalog_service.list_services(db)
    catalog_candidates = _catalog_host_metric_instances(instances, services)

    try:
        rows = await prom_query("node_uname_info")
    except Exception as exc:  # noqa: BLE001 - 转成 Agent 和页面可展示的诊断
        if catalog_candidates:
            return None, (
                f"服务器 {server.name} 的服务目录登记了 node-exporter 指标实例："
                f"{', '.join(catalog_candidates)}，但查询 Prometheus node_uname_info "
                f"验证失败：{exc}。不能确认这些实例当前真的在提供主机指标。"
            )
        return None, (
            f"服务器 {server.name} 未登记 node-exporter 指标实例，自动查询 Prometheus "
            f"node_uname_info 失败：{exc}。"
        )

    observed = _prometheus_host_metric_instances(server.ip_address, rows)
    verified_catalog = tuple(sorted(set(catalog_candidates) & set(observed)))
    if len(verified_catalog) == 1:
        return verified_catalog[0], None
    if len(verified_catalog) > 1:
        return None, (
            f"服务器 {server.name} 的服务目录与 Prometheus node_uname_info 同时确认了"
            f"多个主机指标实例：{', '.join(verified_catalog)}。请在服务目录中只保留准确的 "
            "node-exporter metric_instance。"
        )
    if catalog_candidates:
        if observed:
            return None, (
                f"服务器 {server.name} 的 node-exporter 服务目录实例 "
                f"{', '.join(catalog_candidates)} 与 Prometheus node_uname_info 实际返回的 "
                f"{', '.join(observed)} 不一致。请修正服务目录 metric_instance。"
            )
        return None, (
            f"服务器 {server.name} 的服务目录登记了 node-exporter 指标实例："
            f"{', '.join(catalog_candidates)}，但 Prometheus node_uname_info 未确认它们"
            "当前正在提供主机指标。"
        )
    if len(observed) == 1:
        return observed[0], None
    if len(observed) > 1:
        return None, (
            f"Prometheus 的 node_uname_info 为服务器 {server.name} 返回多个主机指标实例："
            f"{', '.join(observed)}。请在服务目录的 node-exporter 实例上填写唯一的 "
            "metric_instance。"
        )
    return None, (
        f"服务器 {server.name} 既没有在服务目录登记 node-exporter 的 metric_instance，"
        "Prometheus node_uname_info 也没有返回与该服务器 IP 匹配的实例。"
    )
