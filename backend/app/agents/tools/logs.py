"""模拟日志检索工具。

项目还没接入真实日志系统（ELK/Loki 之类），先用固定模板数据模拟"查日志"这个动作，
把多工具调用的链路跑起来；接入真实日志后端是单独的任务，到时候换掉这个函数的实现即可，
工具的名字/参数/调用方式不用变。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.services import server_service


@tool
async def search_logs(server_name: str, keyword: str = "") -> str:
    """搜索指定服务器最近的日志，可选按关键词过滤，返回匹配的日志行。

    注意：项目还没有接入真实日志系统，这里返回的是模拟数据，不代表服务器真实状态。
    """
    async with AsyncSessionLocal() as db:
        servers = await server_service.list_servers(db)
    if not any(s.name == server_name for s in servers):
        return f"没有找到名为 {server_name} 的服务器，请先确认服务器名称。"

    if keyword:
        return (
            f"（模拟数据，未接入真实日志系统）{server_name} 上包含关键词 \"{keyword}\" 的最近日志：\n"
            f"- [WARN] 处理包含 \"{keyword}\" 的请求耗时超过 800ms，已自动重试 1 次\n"
            f"- [INFO] 包含 \"{keyword}\" 的请求处理完成，耗时 210ms"
        )
    return (
        f"（模拟数据，未接入真实日志系统）{server_name} 最近日志：\n"
        f"- [INFO] 健康检查通过\n"
        f"- [WARN] 磁盘使用率达到 78%\n"
        f"- [INFO] 定时任务执行完成"
    )
