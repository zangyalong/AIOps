"""从服务目录生成并审批应用目标服务器 Alloy 日志配置。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

import asyncssh
from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from app.db.session import AsyncSessionLocal
from app.services import server_service, service_catalog_service
from app.services.ssh_service import (
    SSHDeployNotConfigured,
    SSHKeyMissing,
    build_deploy_connect_params,
)

RuntimeType = Literal["systemd", "docker", "process"]
LogSource = Literal["journal", "docker", "none"]
_SAFE_SERVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_SAFE_ENVIRONMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}")
_SAFE_RUNTIME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,199}")
_HASH = re.compile(r"[0-9a-f]{64}")
_MAX_MAPPINGS = 100
_REMOTE_TEMP_MAP = "/var/lib/aioms-deploy/incoming/.services.map.upload"
_REMOTE_MAP = "/var/lib/aioms-deploy/incoming/services.map"
_APPLY_COMMAND = "sudo -n -- /usr/local/sbin/aioms-target-deploy apply-alloy"


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
    preview_hash: str = Field(min_length=64, max_length=64)
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
        "# 由 AIOMS 服务目录自动生成；不要直接编辑。",
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


async def _build_snapshot(server_name: str) -> tuple[object | None, LogConfigSnapshot | None]:
    warnings: list[str] = []
    mappings: list[LogMappingItem] = []
    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, server_name)
        if server is None:
            return None, None
        instances = await service_catalog_service.list_instances(db, server_id=server.id)
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
            if instance.runtime_type == "systemd" and not instance.runtime_name.endswith(
                ".service"
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

    mappings.sort(
        key=lambda item: (
            item.environment,
            item.service_name,
            item.runtime_type,
            item.runtime_name,
        )
    )
    if len(mappings) > _MAX_MAPPINGS:
        warnings.append(
            f"服务实例超过 {_MAX_MAPPINGS} 项，只生成前 {_MAX_MAPPINGS} 项；请分拆服务器或人工审核"
        )
        mappings = mappings[:_MAX_MAPPINGS]
    services_map = _render_services_map(mappings)
    preview_hash = hashlib.sha256(services_map.encode("utf-8")).hexdigest()
    source_count = sum(item.log_source != "none" for item in mappings)
    process_count = sum(item.log_source == "none" for item in mappings)
    if process_count:
        warnings.append(
            f"{process_count} 个未纳管进程无法自动推断日志文件，保留为 none，不会采集日志"
        )
    if any(item.log_source == "docker" for item in mappings):
        warnings.append(
            "包含 Docker 日志；目标服务器首次配置必须显式允许 ALLOW_DOCKER_SOCKET=true"
        )
    snapshot = LogConfigSnapshot(
        server_name=server.name,
        mappings=tuple(mappings),
        services_map=services_map,
        preview_hash=preview_hash,
        source_count=source_count,
        requires_docker_socket=any(item.log_source == "docker" for item in mappings),
        warnings=tuple(warnings),
    )
    return server, snapshot


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
    推断日志文件而标记为 none。返回的 preview_hash 和 mappings 必须原样交给写 Tool。
    """
    _server, snapshot = await _build_snapshot(server_name)
    if snapshot is None:
        return f"没有找到名称或 IP 为 {server_name} 的服务器，请先确认服务器。"
    payload = _snapshot_payload(snapshot)
    if not snapshot.mappings:
        payload["next_action"] = "该服务器没有带运行时身份的服务实例，请先发现并注册服务。"
    elif snapshot.source_count == 0:
        payload["next_action"] = "当前只有 process 实例，无法自动生成可采集日志源。"
    else:
        payload["next_action"] = (
            "确认预览后，调用 apply_server_log_config；必须原样携带 preview_hash 和 mappings。"
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


@tool(args_schema=ApplyServerLogConfigInput)
async def apply_server_log_config(
    server_name: str,
    preview_hash: str,
    mappings: list[LogMappingItem],
) -> str:
    """审批后应用服务目录生成的 Alloy 配置，并启动或重载 Alloy。

    工具会重新读取服务目录并核对预览哈希与每项映射；不会接受任意配置正文。核对成功后使用
    服务器独立部署密钥上传到固定暂存路径，再调用唯一允许的 root 网关子命令。
    """
    server, snapshot = await _build_snapshot(server_name)
    if server is None or snapshot is None:
        return f"没有找到名称或 IP 为 {server_name} 的服务器，请先确认服务器。"
    if snapshot.source_count == 0:
        return "当前服务目录没有可自动采集的 systemd 或 Docker 日志源，未修改服务器。"

    approved_mappings = [item.model_dump() for item in mappings]
    current_mappings = [item.model_dump() for item in snapshot.mappings]
    if preview_hash != snapshot.preview_hash or approved_mappings != current_mappings:
        return (
            "服务目录已变化，当前配置与审批前预览不一致，未修改服务器。"
            "请重新调用 preview_server_log_config 并再次确认。"
        )

    try:
        params = build_deploy_connect_params(server)
    except SSHDeployNotConfigured:
        return (
            f"{snapshot.server_name} 没有配置部署 SSH 身份"
            "（deploy_ssh_user / deploy_ssh_key_filename），未修改服务器。"
        )
    except SSHKeyMissing as exc:
        return f"{exc}，请确认部署私钥已经放到 {exc.key_path.parent} 目录下。"

    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(_REMOTE_TEMP_MAP, "w") as remote_file:
                    await remote_file.write(snapshot.services_map)
                await sftp.chmod(_REMOTE_TEMP_MAP, 0o600)
                await sftp.posix_rename(_REMOTE_TEMP_MAP, _REMOTE_MAP)
            result = await conn.run(_APPLY_COMMAND, check=False, timeout=120)
    except TimeoutError:
        return "上传或应用 Alloy 配置超过 120 秒，结果未知，请人工检查目标服务器。"
    except (asyncssh.Error, OSError) as exc:
        return f"连接、上传或应用 {snapshot.server_name} 的 Alloy 配置失败：{exc}"

    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.exit_status != 0:
        return (
            f"{snapshot.server_name} 的 Alloy 配置应用失败（exit={result.exit_status}）："
            f"{output or '无输出'}"
        )
    return (
        f"已在 {snapshot.server_name} 应用 {snapshot.source_count} 个日志源并重载 Alloy；"
        f"配置摘要 {snapshot.preview_hash[:12]}。{output}"
    )
