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


ValidateContent = Callable[[str], str | None]
ResolveServer = Callable[[str], Awaitable[tuple[Server | None, str | None]]]
ReadSnapshot = Callable[
    [Server, str], Awaitable[tuple[RemoteFileSnapshot | None, str | None]]
]
BuildPreview = Callable[[RemoteFileSnapshot, str], dict[str, Any]]
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
    if (
        snapshot.sha256 != expected_sha256
        or current_preview["preview_hash"] != preview_hash
        or current_preview["preview_diff"] != preview_diff
    ):
        return OperationResult.failure(
            "文件内容或审批差异已变化，请重新生成预览并再次确认。",
            code="preview_changed",
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
