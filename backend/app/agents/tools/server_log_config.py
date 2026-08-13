"""从服务目录生成并审批应用目标服务器 Alloy 日志配置。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from app.db.session import AsyncSessionLocal
from app.operations.executors import log_config as log_config_executor
from app.operations.schemas import OperationCall, OperationContext, OperationResult
from app.services import server_service, service_catalog_service

RuntimeType = Literal["systemd", "docker", "process"]
LogSource = Literal["journal", "docker", "none"]
_SAFE_SERVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_SAFE_ENVIRONMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}")
_SAFE_RUNTIME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,199}")
_HASH = re.compile(r"[0-9a-f]{64}")
_MAX_MAPPINGS = 100


class PreviewServerLogConfigInput(BaseModel):
    server_name: str = Field(min_length=1, max_length=100)

    @field_validator("server_name")
    @classmethod
    def clean_server_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


class LogMappingItem(BaseModel):
    service_name: str
    environment: str
    runtime_type: RuntimeType
    runtime_name: str
    log_source: LogSource


class ApplyServerLogConfigInput(BaseModel):
    server_name: str = Field(min_length=1, max_length=100)
    preview_hash: str = Field(
        default="",
        max_length=64,
        description="由后端审批前按本次所选 mappings 填充，Agent 不要自行生成",
    )
    mappings: list[LogMappingItem] = Field(min_length=1, max_length=_MAX_MAPPINGS)

    @field_validator("server_name")
    @classmethod
    def clean_server_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("preview_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            return ""
        if not _HASH.fullmatch(value):
            raise ValueError("必须是 64 位 SHA-256 十六进制摘要")
        return value


@dataclass(frozen=True)
class LogConfigSnapshot:
    server_name: str
    mappings: tuple[LogMappingItem, ...]
    services_map: str
    preview_hash: str
    source_count: int
    requires_docker_socket: bool
    warnings: tuple[str, ...]


def _mapping_source(runtime_type: str) -> LogSource:
    if runtime_type == "systemd":
        return "journal"
    if runtime_type == "docker":
        return "docker"
    return "none"


def _render_services_map(mappings: list[LogMappingItem]) -> str:
    lines = [
        "# 由 AIOps 服务目录自动生成；不要直接编辑。",
        "# service|environment|runtime_type|runtime_name|log_source|log_path",
    ]
    lines.extend(
        "|".join(
            (
                item.service_name,
                item.environment,
                item.runtime_type,
                item.runtime_name,
                item.log_source,
                "-",
            )
        )
        for item in mappings
    )
    return "\n".join(lines) + "\n"


def _mapping_key(item: LogMappingItem) -> tuple[str, str, str, str, str]:
    return (
        item.service_name,
        item.environment,
        item.runtime_type,
        item.runtime_name,
        item.log_source,
    )


def _mapping_sort_key(item: LogMappingItem) -> tuple[str, str, str, str]:
    return (
        item.environment,
        item.service_name,
        item.runtime_type,
        item.runtime_name,
    )


def _snapshot_from_mappings(
    server_name: str,
    mappings: Sequence[LogMappingItem],
    *,
    warnings: Sequence[str] = (),
) -> LogConfigSnapshot:
    selected = sorted(mappings, key=_mapping_sort_key)
    snapshot_warnings = list(warnings)
    process_count = sum(item.log_source == "none" for item in selected)
    if process_count:
        snapshot_warnings.append(
            f"{process_count} 个未纳管进程无法自动推断日志文件，保留为 none，不会采集日志"
        )
    requires_docker_socket = any(item.log_source == "docker" for item in selected)
    if requires_docker_socket:
        snapshot_warnings.append(
            "包含 Docker 日志；目标服务器首次配置必须显式允许 ALLOW_DOCKER_SOCKET=true"
        )
    services_map = _render_services_map(selected)
    return LogConfigSnapshot(
        server_name=server_name,
        mappings=tuple(selected),
        services_map=services_map,
        preview_hash=hashlib.sha256(services_map.encode("utf-8")).hexdigest(),
        source_count=sum(item.log_source != "none" for item in selected),
        requires_docker_socket=requires_docker_socket,
        warnings=tuple(snapshot_warnings),
    )


def _select_snapshot(
    snapshot: LogConfigSnapshot,
    requested_mappings: Sequence[LogMappingItem],
) -> LogConfigSnapshot:
    """从当前全量目录中核验并冻结本次实际选择的日志映射。"""
    current_by_key = {_mapping_key(item): item for item in snapshot.mappings}
    selected: list[LogMappingItem] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for requested in requested_mappings:
        key = _mapping_key(requested)
        if key in seen:
            raise ValueError(f"日志映射重复：{requested.service_name}")
        seen.add(key)
        current = current_by_key.get(key)
        if current is None:
            raise ValueError(
                f"日志映射已不在当前服务目录：{requested.service_name} ← "
                f"{requested.runtime_type}/{requested.runtime_name}"
            )
        selected.append(current)
    if not selected:
        raise ValueError("至少选择一个日志映射")
    return _snapshot_from_mappings(snapshot.server_name, selected)


async def _build_snapshot(
    server_name: str,
) -> tuple[object | None, LogConfigSnapshot | None]:
    warnings: list[str] = []
    mappings: list[LogMappingItem] = []
    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, server_name)
        if server is None:
            return None, None
        instances = await service_catalog_service.list_instances(
            db, server_id=server.id
        )
        for instance in instances:
            if not instance.runtime_type or not instance.runtime_name:
                warnings.append(f"实例 {instance.name} 没有运行时身份，已跳过")
                continue
            service = await service_catalog_service.get_service(db, instance.service_id)
            if service is None:
                warnings.append(f"实例 {instance.name} 对应的逻辑服务不存在，已跳过")
                continue
            if not _SAFE_SERVICE.fullmatch(service.name):
                warnings.append(f"服务 {service.name} 名称不适合 Alloy 标签，已跳过")
                continue
            if not _SAFE_ENVIRONMENT.fullmatch(service.environment):
                warnings.append(
                    f"服务 {service.name} 的环境 {service.environment} 格式不合法，已跳过"
                )
                continue
            if not _SAFE_RUNTIME.fullmatch(instance.runtime_name):
                warnings.append(f"实例 {instance.name} 的运行对象格式不合法，已跳过")
                continue
            if (
                instance.runtime_type == "systemd"
                and not instance.runtime_name.endswith(".service")
            ):
                warnings.append(
                    f"systemd 实例 {instance.name} 不是准确的 .service 单元，已跳过"
                )
                continue
            if instance.runtime_type not in {"systemd", "docker", "process"}:
                warnings.append(f"实例 {instance.name} 的运行类型不受支持，已跳过")
                continue
            mappings.append(
                LogMappingItem(
                    service_name=service.name,
                    environment=service.environment,
                    runtime_type=instance.runtime_type,
                    runtime_name=instance.runtime_name,
                    log_source=_mapping_source(instance.runtime_type),
                )
            )

    mappings.sort(key=_mapping_sort_key)
    if len(mappings) > _MAX_MAPPINGS:
        warnings.append(
            f"服务实例超过 {_MAX_MAPPINGS} 项，只生成前 {_MAX_MAPPINGS} 项；请分拆服务器或人工审核"
        )
        mappings = mappings[:_MAX_MAPPINGS]
    snapshot = _snapshot_from_mappings(
        server.name,
        mappings,
        warnings=warnings,
    )
    return server, snapshot


async def resolve_apply_server_log_config_call(
    call: OperationCall,
    context: OperationContext,
) -> OperationCall:
    """审批前按本次所选 mappings 重新核验目录并生成专属配置哈希。"""
    del context
    args = call.normalized_args()
    server, full_snapshot = await _build_snapshot(str(args["server_name"]))
    if server is None or full_snapshot is None:
        raise ValueError(
            f"没有找到名称或 IP 为 {args['server_name']} 的服务器，请先确认服务器"
        )
    requested = [LogMappingItem.model_validate(item) for item in args["mappings"]]
    selected_snapshot = _select_snapshot(full_snapshot, requested)
    if selected_snapshot.source_count == 0:
        raise ValueError("所选映射没有可自动采集的 systemd 或 Docker 日志源")
    args.update(
        {
            "server_name": selected_snapshot.server_name,
            "preview_hash": selected_snapshot.preview_hash,
            "mappings": [item.model_dump() for item in selected_snapshot.mappings],
        }
    )
    return OperationCall(id=call.id, name=call.name, args=args)


def _snapshot_payload(snapshot: LogConfigSnapshot) -> dict[str, object]:
    return {
        "server_name": snapshot.server_name,
        "preview_hash": snapshot.preview_hash,
        "source_count": snapshot.source_count,
        "requires_docker_socket": snapshot.requires_docker_socket,
        "mappings": [item.model_dump() for item in snapshot.mappings],
        "services_map": snapshot.services_map,
        "warnings": list(snapshot.warnings),
    }


@tool(args_schema=PreviewServerLogConfigInput)
async def preview_server_log_config(server_name: str) -> str:
    """从已登记的服务实例生成目标服务器 Alloy 日志配置预览，不连接或修改服务器。

    systemd 自动映射 journald，Docker 自动映射容器 stdout/stderr，未纳管 process 因无法可靠
    推断日志文件而标记为 none。用户可以选择全部或部分 mappings；写操作进入审批前，后端会
    根据当前服务目录核验所选项，并为这组实际要应用的映射重新生成专属哈希。
    """
    _server, snapshot = await _build_snapshot(server_name)
    if snapshot is None:
        return f"没有找到名称或 IP 为 {server_name} 的服务器，请先确认服务器。"
    payload = _snapshot_payload(snapshot)
    if not snapshot.mappings:
        payload["next_action"] = (
            "该服务器没有带运行时身份的服务实例，请先发现并注册服务。"
        )
    elif snapshot.source_count == 0:
        payload["next_action"] = "当前只有 process 实例，无法自动生成可采集日志源。"
    else:
        payload["next_action"] = (
            "确认预览后，调用 apply_server_log_config；mappings 只提交本次确实要应用的映射，"
            "preview_hash 由后端在审批前按所选映射重新生成。"
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


@tool(args_schema=ApplyServerLogConfigInput)
async def apply_server_log_config(
    server_name: str,
    mappings: list[LogMappingItem],
    preview_hash: str = "",
) -> OperationResult:
    """审批后应用服务目录生成的 Alloy 配置，并启动或重载 Alloy。

    审批前后都会重新读取服务目录，仅对本次所选 mappings 生成并核对配置哈希；目录中未选择的
    其他服务不会造成假冲突，所选服务发生变化仍会拒绝执行。工具不会接受任意配置正文。核对成功后使用
    与只读查询共用服务器的非 root AIOps SSH 身份上传到固定暂存路径，再调用唯一允许的
    root 网关子命令。SSH 身份本身不能执行任意 root 命令。
    """
    return await log_config_executor.apply_server_log_config(
        server_name,
        preview_hash,
        mappings,
        build_snapshot=_build_snapshot,
        select_snapshot=_select_snapshot,
    )
