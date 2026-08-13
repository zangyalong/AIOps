from __future__ import annotations

from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.models.service_catalog import Service
from app.services import server_service, service_catalog_service


@tool
async def query_server_services(server: str) -> str:
    """查询服务目录中绑定到指定服务器的部署实例。

    server 可以是 servers 表中的服务器名称或 IP。结果只代表 AIOps 服务目录里的登记关系，
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
            f"runtime={instance.runtime_type or '未配置'}/"
            f"{instance.runtime_name or '未配置'}，"
            f"控制授权={'已开启' if instance.control_enabled else '未开启'}，"
            f"metric_instance={instance.metric_instance or '未配置'}，"
            f"log_service={instance.log_service or '未配置'}"
        )

    if not lines:
        lines.append("- （没有登记绑定到这台服务器的部署实例）")
    return "\n".join(
        [
            f"## {target.name}（{target.ip_address}）上登记的服务",
            *lines,
            "说明：以上来自 AIOps 服务目录，不代表实时进程或容器运行状态。",
        ]
    )


def _label(service: Service) -> str:
    return f"{service.name}/{service.environment}"
