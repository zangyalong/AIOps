from __future__ import annotations

from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.services import server_service


@tool
async def query_servers() -> str:
    """查询 servers 表里所有服务器的名称、IP 地址和描述，返回文本列表。"""
    async with AsyncSessionLocal() as db:
        servers = await server_service.list_servers(db)
    if not servers:
        return "servers 表目前没有数据。"
    lines = [
        f"- {s.name} ({s.ip_address})：{s.description or '无描述'}" for s in servers
    ]
    return "\n".join(lines)
