"""应用目标服务器 Alloy 日志配置的执行器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import asyncssh

from app.operations.schemas import OperationResult
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

REMOTE_TEMP_MAP = "/var/lib/aioms/incoming/.services.map.upload"
REMOTE_MAP = "/var/lib/aioms/incoming/services.map"
APPLY_COMMAND = "sudo -n -- /usr/local/sbin/aioms-target-deploy apply-alloy"


class LogConfigSnapshot(Protocol):
    server_name: str
    mappings: Sequence[Any]
    services_map: str
    preview_hash: str
    source_count: int


BuildSnapshot = Callable[[str], Awaitable[tuple[Any | None, LogConfigSnapshot | None]]]
SelectSnapshot = Callable[[LogConfigSnapshot, Sequence[Any]], LogConfigSnapshot]


async def apply_server_log_config(
    server_name: str,
    preview_hash: str,
    mappings: Sequence[Any],
    *,
    build_snapshot: BuildSnapshot,
    select_snapshot: SelectSnapshot,
) -> OperationResult:
    server, full_snapshot = await build_snapshot(server_name)
    if server is None or full_snapshot is None:
        return OperationResult.failure(
            f"没有找到名称或 IP 为 {server_name} 的服务器，请先确认服务器。",
            code="server_not_found",
        )
    try:
        snapshot = select_snapshot(full_snapshot, mappings)
    except ValueError as exc:
        return OperationResult.failure(
            f"服务目录已变化，{exc}，未修改服务器。请重新生成预览并再次确认。",
            code="preview_changed",
        )
    if snapshot.source_count == 0:
        return OperationResult.failure(
            "当前服务目录没有可自动采集的 systemd 或 Docker 日志源，未修改服务器。",
            code="no_log_sources",
        )

    if preview_hash != snapshot.preview_hash:
        return OperationResult.failure(
            "所选日志配置与审批前预览不一致，未修改服务器。请重新生成预览并再次确认。",
            code="preview_changed",
        )

    try:
        params = build_connect_params(server)
    except SSHNotConfigured:
        return OperationResult.failure(
            f"{snapshot.server_name} 没有配置 AIOMS SSH 身份"
            "（ssh_user / ssh_key_filename），未修改服务器。",
            code="ssh_not_configured",
        )
    except SSHKeyMissing as exc:
        return OperationResult.failure(
            f"{exc}，请确认 AIOMS 私钥已经放到 {exc.key_path.parent} 目录下。",
            code="ssh_key_missing",
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
                async with sftp.open(REMOTE_TEMP_MAP, "w") as remote_file:
                    await remote_file.write(snapshot.services_map)
                await sftp.chmod(REMOTE_TEMP_MAP, 0o600)
                await sftp.posix_rename(REMOTE_TEMP_MAP, REMOTE_MAP)
            result = await conn.run(APPLY_COMMAND, check=False, timeout=120)
    except TimeoutError:
        return OperationResult.failure(
            "上传或应用 Alloy 配置超过 120 秒，结果未知，请人工检查目标服务器。",
            code="execution_timeout",
        )
    except (asyncssh.Error, OSError) as exc:
        return OperationResult.failure(
            f"连接、上传或应用 {snapshot.server_name} 的 Alloy 配置失败：{exc}",
            code="ssh_failed",
        )

    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.exit_status != 0:
        return OperationResult.failure(
            f"{snapshot.server_name} 的 Alloy 配置应用失败（exit={result.exit_status}）："
            f"{output or '无输出'}",
            code="gateway_failed",
        )
    return OperationResult.success(
        f"已在 {snapshot.server_name} 应用 {snapshot.source_count} 个日志源并重载 Alloy；"
        f"配置摘要 {snapshot.preview_hash[:12]}。{output}"
    )
