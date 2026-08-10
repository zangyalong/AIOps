"""服务控制 Agent 工具定义；具体执行由 operations executor 完成。"""

from __future__ import annotations

from langchain_core.tools import tool

from app.operations.executors import service_control
from app.operations.executors.service_control import RuntimeType
from app.operations.schemas import OperationResult


@tool
async def start_service(
    server_name: str,
    service_name: str,
    runtime_type: RuntimeType = "systemd",
) -> OperationResult:
    """启动指定服务器上明确加入白名单的 systemd 服务或 Docker 容器。

    这是中风险写操作：必须先由用户在审批卡片中确认，并通过目标服务器固定写网关执行。
    """
    return await service_control.control_service(
        "start", server_name, service_name, runtime_type
    )


@tool
async def stop_service(
    server_name: str,
    service_name: str,
    runtime_type: RuntimeType = "systemd",
) -> OperationResult:
    """停止指定服务器上明确加入白名单的 systemd 服务或 Docker 容器。

    这是高风险写操作：必须经过审批卡片和详情弹窗两步确认，再通过固定写网关执行。
    """
    return await service_control.control_service(
        "stop", server_name, service_name, runtime_type
    )


@tool
async def restart_service(
    server_name: str,
    service_name: str,
    runtime_type: RuntimeType = "systemd",
) -> OperationResult:
    """重启指定服务器上明确加入白名单的 systemd 服务或 Docker 容器。

    这是高风险写操作：必须经过审批卡片和详情弹窗两步确认，再通过固定写网关执行。
    """
    return await service_control.control_service(
        "restart", server_name, service_name, runtime_type
    )
