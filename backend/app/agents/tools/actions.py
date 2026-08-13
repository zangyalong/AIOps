"""服务控制 Agent 工具定义；具体执行由 operations executor 完成。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool
from pydantic import BaseModel, Field, field_validator

from app.operations.executors import compose_recreate, service_control
from app.operations.executors.service_control import RuntimeType
from app.operations.schemas import OperationResult


class ServiceControlInput(BaseModel):
    """模型只表达业务目标；运行时身份由审批前的目录解析器注入。"""

    server_name: str = Field(
        min_length=1,
        max_length=100,
        description="已纳管服务器名称或 IP，例如 web-01。",
    )
    service_name: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "AIOMS 服务目录中的业务服务名，例如 pixel-forge-frontend；"
            "不要填写 .service 单元名或 Docker 容器名。"
        ),
    )
    environment: str = Field(
        default="",
        max_length=50,
        description="同名服务存在多个环境时填写，例如 prod；不确定时先查询服务目录。",
    )
    instance_name: str = Field(
        default="",
        max_length=200,
        description="同一服务在该服务器存在多个实例时填写目录中的实例名。",
    )
    service_instance_id: Annotated[str, InjectedToolArg] = ""
    runtime_type: Annotated[RuntimeType | None, InjectedToolArg] = None
    runtime_name: Annotated[str, InjectedToolArg] = ""

    @field_validator("server_name", "service_name", "environment", "instance_name")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()


async def _control(
    action: service_control.ServiceAction,
    server_name: str,
    service_name: str,
    environment: str,
    instance_name: str,
    service_instance_id: str,
    runtime_type: RuntimeType | None,
    runtime_name: str,
) -> OperationResult:
    del service_name, environment, instance_name
    if not service_instance_id or runtime_type is None or not runtime_name:
        return OperationResult.failure(
            "服务控制缺少审批前解析出的目录实例，已拒绝执行。请重新发起操作。",
            code="unresolved_service_target",
        )
    return await service_control.control_resolved_service(
        action,
        server_name=server_name,
        service_instance_id=service_instance_id,
        runtime_type=runtime_type,
        runtime_name=runtime_name,
    )


@tool(args_schema=ServiceControlInput)
async def start_service(
    server_name: str,
    service_name: str,
    environment: str = "",
    instance_name: str = "",
    service_instance_id: str = "",
    runtime_type: RuntimeType | None = None,
    runtime_name: str = "",
) -> OperationResult:
    """启动目录中唯一匹配且已开启控制授权的业务服务。

    只提交业务服务名；系统会在审批前从目录解析并冻结真实 systemd 单元或 Docker 容器。
    这是中风险写操作，必须先由用户确认。
    """
    return await _control(
        "start",
        server_name,
        service_name,
        environment,
        instance_name,
        service_instance_id,
        runtime_type,
        runtime_name,
    )


@tool(args_schema=ServiceControlInput)
async def stop_service(
    server_name: str,
    service_name: str,
    environment: str = "",
    instance_name: str = "",
    service_instance_id: str = "",
    runtime_type: RuntimeType | None = None,
    runtime_name: str = "",
) -> OperationResult:
    """停止目录中唯一匹配且已开启控制授权的业务服务。

    只提交业务服务名；系统会在审批前解析真实运行对象。此操作必须经过审批卡片和详情弹窗两步确认。
    """
    return await _control(
        "stop",
        server_name,
        service_name,
        environment,
        instance_name,
        service_instance_id,
        runtime_type,
        runtime_name,
    )


@tool(args_schema=ServiceControlInput)
async def restart_service(
    server_name: str,
    service_name: str,
    environment: str = "",
    instance_name: str = "",
    service_instance_id: str = "",
    runtime_type: RuntimeType | None = None,
    runtime_name: str = "",
) -> OperationResult:
    """重启目录中唯一匹配且已开启控制授权的业务服务。

    只提交业务服务名；系统会在审批前解析真实运行对象。此操作必须经过审批卡片和详情弹窗两步确认。
    """
    return await _control(
        "restart",
        server_name,
        service_name,
        environment,
        instance_name,
        service_instance_id,
        runtime_type,
        runtime_name,
    )


@tool
async def recreate_compose_service(
    server_name: str,
    container_name: str,
) -> OperationResult:
    """Recreate 已登记且已授权的单个 Docker Compose 服务。

    用于应用 `.env` 插值、端口映射等只有重建容器才会生效的变更。
    这是高风险写操作：必须经过审批卡片和详情弹窗两步确认。工具不接受
    Compose 路径或附加参数；目标机固定网关从容器标签反查准确项目。
    """
    return await compose_recreate.recreate_compose_service(server_name, container_name)
