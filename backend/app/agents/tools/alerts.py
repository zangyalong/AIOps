"""告警查询工具，查的是 alert_events 真实表。

表里的数据现在靠 scripts/seed_alerts.py 手动模拟生成，还没接真实监控系统（Prometheus
Alertmanager 之类）产生实时数据——查询本身是真的，只是数据来源是种子脚本而不是活的监控系统。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.services import alert_event_service, server_service


@tool
async def query_alerts(server_name: str = "") -> str:
    """查询当前未处理（status=open）的告警，不传 server_name 则返回所有服务器的告警。

    注意：alert_events 表里的数据是手动模拟生成的（见 scripts/seed_alerts.py），
    还没接入真实监控系统产生的实时告警，回答用户时要说明这一点。
    """
    async with AsyncSessionLocal() as db:
        servers = await server_service.list_servers(db)
        server_by_name = {s.name: s for s in servers}

        if server_name and server_name not in server_by_name:
            return f"没有找到名为 {server_name} 的服务器，请先确认服务器名称。"

        target_id = server_by_name[server_name].id if server_name else None
        alerts = await alert_event_service.list_open_alerts(db, target_id)

    if not alerts:
        scope = f"{server_name} 当前" if server_name else "当前"
        return f"（数据来自模拟生成，未接入真实监控系统）{scope}没有未处理的告警。"

    id_to_name = {s.id: s.name for s in servers}
    lines = [
        f"- [{a.severity}] {id_to_name.get(a.server_id, '（无关联服务器）')}：{a.message}"
        for a in alerts
    ]
    return "（数据来自模拟生成，未接入真实监控系统）当前未处理告警：\n" + "\n".join(lines)
