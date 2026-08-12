"""目标服务器文本文件更新执行器。"""

from __future__ import annotations

import secrets
import shlex
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import asyncssh

from app.models.server import Server
from app.operations.schemas import OperationResult
from app.services.ssh_service import SSHConnectParams

FILE_WRITE_GATEWAY = "/usr/local/sbin/aioms-file-write"
REMOTE_INCOMING_DIR = "/var/lib/aioms/file-incoming"


class RemoteFileSnapshot(Protocol):
    path: str
    sha256: str
    redacted: bool


class ConfigPatchPreview(Protocol):
    path: str
    expected_sha256: str
    preview_hash: str
    preview_diff: str
    changed: bool


ValidateContent = Callable[[str], str | None]
ResolveServer = Callable[[str], Awaitable[tuple[Server | None, str | None]]]
ReadSnapshot = Callable[
    [Server, str], Awaitable[tuple[RemoteFileSnapshot | None, str | None]]
]
BuildPreview = Callable[[RemoteFileSnapshot, str], dict[str, Any]]
ValidatePatch = Callable[[str, str, str, object], str | None]
PreviewPatch = Callable[
    [Server, str, str, str, object],
    Awaitable[tuple[ConfigPatchPreview | None, str | None]],
]
EncodeValue = Callable[[object], str]
ConnectParams = Callable[[Server], tuple[SSHConnectParams | None, str | None]]


async def update_server_file(
    server_name: str,
    path: str,
    new_content: str,
    expected_sha256: str,
    preview_hash: str,
    preview_diff: str,
    *,
    validate_content: ValidateContent,
    resolve_server: ResolveServer,
    read_snapshot: ReadSnapshot,
    build_preview: BuildPreview,
    connect_params: ConnectParams,
) -> OperationResult:
    content_error = validate_content(new_content)
    if content_error:
        return OperationResult.failure(
            f"已拒绝写入：{content_error}。", code="invalid_content"
        )
    server, error = await resolve_server(server_name)
    if server is None:
        return OperationResult.failure(error or "服务器不存在", code="server_not_found")
    snapshot, read_error = await read_snapshot(server, path)
    if snapshot is None:
        return OperationResult.failure(read_error or "读取失败", code="read_failed")
    if snapshot.redacted:
        return OperationResult.failure(
            "当前文件包含敏感字段，禁止通过通用工具整文件覆盖。",
            code="sensitive_content",
        )
    current_preview = build_preview(snapshot, new_content)
    if not current_preview["changed"]:
        return OperationResult.success(
            "新旧内容相同，没有修改服务器。", code="no_change"
        )
    if current_preview["diff_truncated"]:
        return OperationResult.failure(
            "完整差异超过审批展示上限，已拒绝通过通用工具写入。",
            code="diff_too_large",
        )
    changed_fields = [
        name
        for name, changed in (
            ("expected_sha256", snapshot.sha256 != expected_sha256),
            ("preview_hash", current_preview["preview_hash"] != preview_hash),
            ("preview_diff", current_preview["preview_diff"] != preview_diff),
        )
        if changed
    ]
    if changed_fields:
        return OperationResult.failure(
            "文件内容或审批差异已变化"
            f"（变化字段：{', '.join(changed_fields)}），请重新生成预览并再次确认。",
            code="preview_changed",
            metadata={"changed_fields": changed_fields},
        )

    params, connect_error = connect_params(server)
    if params is None:
        return OperationResult.failure(
            connect_error or "SSH 配置不可用", code="ssh_not_configured"
        )
    token = secrets.token_hex(16)
    remote_path = f"{REMOTE_INCOMING_DIR}/{token}"
    command = shlex.join(
        (
            "sudo",
            "-n",
            "--",
            FILE_WRITE_GATEWAY,
            "apply",
            snapshot.path,
            snapshot.sha256,
            token,
        )
    )
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
                async with sftp.open(remote_path, "x") as remote_file:
                    await remote_file.write(new_content)
                await sftp.chmod(remote_path, 0o600)
            result = await conn.run(command, check=False, timeout=30)
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return OperationResult.failure(
            f"连接、上传或更新目标服务器文件失败：{exc}",
            code="ssh_failed",
        )
    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.exit_status != 0:
        return OperationResult.failure(
            f"更新文件失败（exit={result.exit_status}）：{output or '无输出'}",
            code="gateway_failed",
        )
    return OperationResult.success(output or f"已更新 {snapshot.path}。")


async def patch_server_config(
    server_name: str,
    path: str,
    config_format: str,
    selector: str,
    value: object,
    expected_sha256: str,
    preview_hash: str,
    preview_diff: str,
    *,
    validate_patch: ValidatePatch,
    resolve_server: ResolveServer,
    preview_patch: PreviewPatch,
    encode_value: EncodeValue,
    connect_params: ConnectParams,
) -> OperationResult:
    validation_error = validate_patch(path, config_format, selector, value)
    if validation_error:
        return OperationResult.failure(
            f"已拒绝结构化配置补丁：{validation_error}。",
            code="invalid_config_patch",
        )
    server, error = await resolve_server(server_name)
    if server is None:
        return OperationResult.failure(error or "服务器不存在", code="server_not_found")
    current_preview, current_error = await preview_patch(
        server, path, config_format, selector, value
    )
    if current_preview is None:
        return OperationResult.failure(
            current_error or "无法重新生成结构化配置预览",
            code="config_preview_failed",
        )
    if not current_preview.changed:
        return OperationResult.success(
            f"{selector} 已是目标值，没有修改服务器。",
            code="no_change",
        )
    changed_fields = [
        name
        for name, changed in (
            (
                "expected_sha256",
                current_preview.expected_sha256 != expected_sha256,
            ),
            ("preview_hash", current_preview.preview_hash != preview_hash),
            ("preview_diff", current_preview.preview_diff != preview_diff),
        )
        if changed
    ]
    if changed_fields:
        return OperationResult.failure(
            "文件内容或审批补丁已变化"
            f"（变化字段：{', '.join(changed_fields)}），请重新生成预览并再次确认。",
            code="preview_changed",
            metadata={"changed_fields": changed_fields},
        )

    params, connect_error = connect_params(server)
    if params is None:
        return OperationResult.failure(
            connect_error or "SSH 配置不可用", code="ssh_not_configured"
        )
    value_json = encode_value(value)
    command = shlex.join(
        (
            "sudo",
            "-n",
            "--",
            FILE_WRITE_GATEWAY,
            "config-apply",
            path,
            expected_sha256,
            config_format,
            selector,
            value_json,
        )
    )
    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            result = await conn.run(command, check=False, timeout=30)
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return OperationResult.failure(
            f"连接或应用目标服务器结构化配置补丁失败：{exc}",
            code="ssh_failed",
        )
    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.exit_status != 0:
        return OperationResult.failure(
            f"应用结构化配置补丁失败（exit={result.exit_status}）：{output or '无输出'}",
            code="gateway_failed",
        )
    return OperationResult.success(output or f"已更新 {path} 中的 {selector}。")
