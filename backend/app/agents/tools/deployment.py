from __future__ import annotations

from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.services import environment_deployment_service, server_service


@tool
async def deploy_server_environment(server_name: str) -> str:
    """把服务器页面保存的环境配置自动部署到目标服务器。

    这是高风险写操作，必须经过人工审批。Tool 不接收或生成数据库密码；已启用数据库
    exporter 时只复用目标服务器现有凭据，首次配置密码必须由用户在服务器页面提交。
    """
    identifier = server_name.strip()
    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, identifier)
        if server is None:
            return f'没有找到名称或 IP 为 "{identifier}" 的已纳管服务器。'
        try:
            detail = await environment_deployment_service.apply_saved_environment(
                db, server
            )
        except environment_deployment_service.EnvironmentDeploymentError as exc:
            return str(exc)
    return f"已自动部署 {server.name} 的环境配置。\n{detail}"
