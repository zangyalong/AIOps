"""目标服务器环境部署 Agent 工具定义。"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agents.tools.tool_schema import ServerIdentifier, ToolInputModel
from app.operations.executors import deployment
from app.operations.schemas import OperationResult


class DeployServerEnvironmentInput(ToolInputModel):
    server_name: ServerIdentifier


@tool(args_schema=DeployServerEnvironmentInput)
async def deploy_server_environment(server_name: str) -> OperationResult:
    """把服务器页面保存的环境配置自动部署到目标服务器。

    这是高风险写操作，必须经过人工审批。Tool 不接收或生成数据库密码；已启用数据库
    exporter 时只复用目标服务器现有凭据，首次配置密码必须由用户在服务器页面提交。
    """
    return await deployment.deploy_server_environment(server_name)
