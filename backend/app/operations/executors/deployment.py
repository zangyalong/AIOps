"""目标服务器环境部署执行器。"""

from __future__ import annotations

from app.db.session import AsyncSessionLocal
from app.operations.schemas import OperationResult
from app.services import environment_deployment_service, server_service


async def deploy_server_environment(server_name: str) -> OperationResult:
    identifier = server_name.strip()
    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, identifier)
        if server is None:
            return OperationResult.failure(
                f'没有找到名称或 IP 为 "{identifier}" 的已纳管服务器。',
                code="server_not_found",
            )
        try:
            detail = await environment_deployment_service.apply_saved_environment(
                db, server
            )
        except environment_deployment_service.EnvironmentDeploymentError as exc:
            return OperationResult.failure(str(exc), code="deployment_failed")
    return OperationResult.success(f"已自动部署 {server.name} 的环境配置。\n{detail}")
