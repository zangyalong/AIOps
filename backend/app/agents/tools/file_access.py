"""由 Agent 发起、经人工审批后执行的精确文件写授权。"""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from app.db.session import AsyncSessionLocal
from app.operations.schemas import OperationResult
from app.services import (
    server_file_policy_service,
    server_service,
    service_catalog_service,
)


def _clean_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


class GrantServerFileWriteAccessInput(BaseModel):
    server_name: str = Field(min_length=1, max_length=100)
    service_name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=4096)
    environment: str = Field(default="", max_length=100)
    instance_name: str = Field(default="", max_length=200)

    _clean_required = field_validator("server_name", "service_name")(_clean_text)

    @field_validator("environment", "instance_name")
    @classmethod
    def clean_optional(cls, value: str) -> str:
        return value.strip()

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            raise ValueError("必须填写绝对路径")
        if any(char in value for char in ("\x00", "\n", "\r")):
            raise ValueError("路径不能包含空字符或换行")
        return value


@tool(args_schema=GrantServerFileWriteAccessInput)
async def grant_server_file_write_access(
    server_name: str,
    service_name: str,
    path: str,
    environment: str = "",
    instance_name: str = "",
) -> OperationResult:
    """为已登记服务授予目标服务器上一个既存普通文本文件的精确写权限。

    只能在用户明确要求修改文件、调查已确定准确服务器、服务和文件路径、且文件预览明确返回未
    授权时调用。它会持久加入目标机 root 白名单和 AIOMS 服务目录，不能授权目录、敏感路径、
    符号链接、不存在的文件或非文本文件。授权本身必须先经人工审批；后续文件修改仍需独立预览
    和审批。
    """
    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, server_name)
        if server is None:
            return OperationResult.failure(
                f"没有找到名称或 IP 为 {server_name} 的服务器。",
                code="server_not_found",
            )

        instances = await service_catalog_service.list_instances(
            db, server_id=server.id
        )
        services = {
            item.id: item for item in await service_catalog_service.list_services(db)
        }
        matches = []
        for instance in instances:
            service = services.get(instance.service_id)
            if service is None or service.name != service_name:
                continue
            if environment and service.environment != environment:
                continue
            if instance_name and instance.name != instance_name:
                continue
            matches.append((service, instance))

        if not matches:
            filters = [f"服务={service_name}"]
            if environment:
                filters.append(f"环境={environment}")
            if instance_name:
                filters.append(f"实例={instance_name}")
            return OperationResult.failure(
                f"{server.name} 的服务目录中没有匹配项（{'，'.join(filters)}）。",
                code="service_instance_not_found",
            )
        if len(matches) > 1:
            choices = "、".join(
                f"{service.name}/{service.environment}/{instance.name}"
                for service, instance in matches
            )
            return OperationResult.failure(
                f"匹配到多个服务实例：{choices}；请补充 environment 或 instance_name。",
                code="ambiguous_service_instance",
            )

        service, instance = matches[0]
        try:
            access = await server_file_policy_service.create_file_access(
                db,
                instance,
                access_level="write",
                path=path,
            )
        except server_file_policy_service.ServerFilePolicySyncError as exc:
            return OperationResult.failure(
                str(exc),
                code="file_access_policy_sync_failed",
            )
        except ValueError as exc:
            return OperationResult.failure(
                str(exc),
                code="invalid_file_access_request",
            )

    return OperationResult.success(
        (
            f"已授权 {server.name} 上的 {service.name}/{service.environment}/"
            f"{instance.name} 精确写入文件 {access.path}。"
        ),
        code="server_file_write_access_granted",
        metadata={
            "access_id": str(access.id),
            "server_name": server.name,
            "service_name": service.name,
            "environment": service.environment,
            "instance_name": instance.name,
            "path": access.path,
        },
    )
