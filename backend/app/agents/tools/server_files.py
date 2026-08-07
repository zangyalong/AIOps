"""通过目标服务器固定网关查找、脱敏读取和受控更新文本配置。"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import secrets
import shlex
from dataclasses import dataclass

import asyncssh
from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from app.db.session import AsyncSessionLocal
from app.models.server import Server
from app.services import server_service
from app.services.ssh_service import (
    SSHConnectParams,
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

FILE_READ_GATEWAY = "/usr/local/sbin/aioms-file-read"
FILE_WRITE_GATEWAY = "/usr/local/sbin/aioms-file-write"
REMOTE_INCOMING_DIR = "/var/lib/aioms/file-incoming"
MAX_FILE_BYTES = 65_536
MAX_DIFF_CHARS = 12_000
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@*?-]{1,100}$")
_SENSITIVE_CONTENT = re.compile(
    r'(?im)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|'
    r'client[_-]?secret|credential|authorization)["\']?\s*[:=]\s*\S+'
    r"|://[^\s/:@]+:[^\s@]+@"
)


def _clean_absolute_path(value: str) -> str:
    value = value.strip()
    if not value.startswith("/"):
        raise ValueError("必须填写绝对路径")
    if any(char in value for char in ("\x00", "\n", "\r")):
        raise ValueError("路径不能包含空字符或换行")
    return value


class FindServerFilesInput(BaseModel):
    server_name: str = Field(min_length=1, max_length=100)
    root_path: str = Field(min_length=1, max_length=4096)
    filename_pattern: str = Field(default="*", min_length=1, max_length=100)

    _clean_root = field_validator("root_path")(_clean_absolute_path)

    @field_validator("server_name")
    @classmethod
    def clean_server_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("filename_pattern")
    @classmethod
    def validate_filename_pattern(cls, value: str) -> str:
        value = value.strip()
        if not _FILENAME_PATTERN.fullmatch(value):
            raise ValueError("文件名模式只允许字母、数字、._@*?-")
        return value


class ReadServerFileInput(BaseModel):
    server_name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)

    _clean_path = field_validator("path")(_clean_absolute_path)

    @field_validator("server_name")
    @classmethod
    def clean_server_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


class PreviewServerFileUpdateInput(ReadServerFileInput):
    new_content: str = Field(max_length=MAX_FILE_BYTES)


class UpdateServerFileInput(PreviewServerFileUpdateInput):
    expected_sha256: str = Field(min_length=64, max_length=64)
    preview_hash: str = Field(min_length=64, max_length=64)
    preview_diff: str = Field(max_length=MAX_DIFF_CHARS)

    @field_validator("expected_sha256", "preview_hash")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        value = value.strip().lower()
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("必须是 64 位 SHA-256 十六进制摘要")
        return value


@dataclass(frozen=True)
class RemoteFileSnapshot:
    path: str
    sha256: str
    redacted: bool
    content: str


def _validate_new_content(content: str) -> str | None:
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return f"新内容超过 {MAX_FILE_BYTES} 字节限制"
    if "\x00" in content:
        return "新内容不是文本文件"
    if "[已脱敏]" in content:
        return "新内容包含脱敏占位符，不能覆盖真实配置"
    if "-----BEGIN " in content and "PRIVATE KEY-----" in content:
        return "禁止通过 Agent 写入私钥"
    if _SENSITIVE_CONTENT.search(content):
        return "新内容疑似包含密码、Token、密钥或带密码的连接串，禁止进入 Agent 工具参数"
    return None


async def _resolve_server(server_name: str) -> tuple[Server | None, str | None]:
    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, server_name)
    if server is None:
        return None, f"没有找到名称或 IP 为 {server_name} 的服务器，请先确认服务器。"
    return server, None


def _connect_params_or_error(
    server: Server,
) -> tuple[SSHConnectParams | None, str | None]:
    try:
        return build_connect_params(server), None
    except SSHNotConfigured:
        return None, "目标服务器没有配置 AIOMS SSH 身份，无法访问文件。"
    except SSHKeyMissing as exc:
        return None, f"{exc}，请确认 AIOMS 私钥位置。"


async def _run_read_gateway(
    server: Server,
    *args: str,
) -> tuple[int, str]:
    params, error = _connect_params_or_error(server)
    if params is None:
        return 255, error or "SSH 配置不可用"
    command = shlex.join(("sudo", "-n", "--", FILE_READ_GATEWAY, *args))
    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            result = await conn.run(command, check=False, timeout=20)
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return 255, f"连接或读取目标服务器失败：{exc}"
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if result.exit_status == 0:
        return 0, stdout
    return result.exit_status, f"{stdout}{stderr}".strip()


def _parse_snapshot(output: str) -> RemoteFileSnapshot | None:
    header, separator, content = output.partition("\n---\n")
    if not separator:
        return None
    metadata: dict[str, str] = {}
    for line in header.splitlines():
        key, equals, value = line.partition("=")
        if equals:
            metadata[key] = value
    path = metadata.get("PATH", "")
    sha256 = metadata.get("SHA256", "")
    redacted = metadata.get("REDACTED")
    if not path.startswith("/") or not _SHA256_PATTERN.fullmatch(sha256):
        return None
    if redacted not in {"true", "false"}:
        return None
    return RemoteFileSnapshot(
        path=path,
        sha256=sha256,
        redacted=redacted == "true",
        content=content,
    )


async def _read_snapshot(
    server: Server, path: str
) -> tuple[RemoteFileSnapshot | None, str | None]:
    status, output = await _run_read_gateway(server, "read", path)
    if status != 0:
        return None, output or f"读取失败（exit={status}）"
    snapshot = _parse_snapshot(output)
    if snapshot is None:
        return None, "目标服务器文件网关返回了无法识别的结果，未继续操作。"
    return snapshot, None


def _build_preview(snapshot: RemoteFileSnapshot, new_content: str) -> dict[str, object]:
    diff = "".join(
        difflib.unified_diff(
            snapshot.content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"{snapshot.path}（当前）",
            tofile=f"{snapshot.path}（拟更新）",
        )
    )
    diff_truncated = len(diff) > MAX_DIFF_CHARS
    if diff_truncated:
        diff = diff[:MAX_DIFF_CHARS]
    preview_hash = hashlib.sha256(
        json.dumps(
            {
                "path": snapshot.path,
                "current_sha256": snapshot.sha256,
                "new_content": new_content,
                "diff": diff,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "path": snapshot.path,
        "expected_sha256": snapshot.sha256,
        "preview_hash": preview_hash,
        "preview_diff": diff or "（内容没有变化）",
        "changed": bool(diff),
        "diff_truncated": diff_truncated,
    }


@tool(args_schema=FindServerFilesInput)
async def find_server_files(
    server_name: str,
    root_path: str,
    filename_pattern: str = "*",
) -> str:
    """在服务实例已授权的目录中查找候选文本配置文件。

    root_path 必须先在服务目录页面登记为可读目录；搜索最多深入五层、返回一百项，不能跨文件系统。
    """
    server, error = await _resolve_server(server_name)
    if server is None:
        return error or "服务器不存在"
    status, output = await _run_read_gateway(
        server, "find", root_path, filename_pattern
    )
    if status != 0:
        return f"查找文件失败（exit={status}）：{output or '无输出'}"
    return output or "授权目录中没有匹配的文件。"


@tool(args_schema=ReadServerFileInput)
async def read_server_file(server_name: str, path: str) -> str:
    """通过 SSH 固定网关读取已授权的服务器文本文件，返回路径、哈希和脱敏内容。

    密码、Token、连接串凭据会按整行脱敏；私钥及系统身份文件始终拒绝读取。
    """
    server, error = await _resolve_server(server_name)
    if server is None:
        return error or "服务器不存在"
    snapshot, read_error = await _read_snapshot(server, path)
    if snapshot is None:
        return read_error or "读取失败"
    return json.dumps(
        {
            "path": snapshot.path,
            "sha256": snapshot.sha256,
            "redacted": snapshot.redacted,
            "content": snapshot.content,
            "notice": (
                "内容含敏感字段，已脱敏；禁止用该内容进行整文件覆盖。"
                if snapshot.redacted
                else "内容未触发敏感字段脱敏。"
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


@tool(args_schema=PreviewServerFileUpdateInput)
async def preview_server_file_update(
    server_name: str,
    path: str,
    new_content: str,
) -> str:
    """读取当前文件并生成统一差异预览，不修改服务器。

    文件只要含脱敏字段，就拒绝整文件预览，以免模型用脱敏占位符覆盖真实凭据。成功返回的哈希和
    preview_diff 必须原样交给 update_server_file。
    """
    content_error = _validate_new_content(new_content)
    if content_error:
        return f"无法生成写入预览：{content_error}。"
    server, error = await _resolve_server(server_name)
    if server is None:
        return error or "服务器不存在"
    snapshot, read_error = await _read_snapshot(server, path)
    if snapshot is None:
        return read_error or "读取失败"
    if snapshot.redacted:
        return "当前文件包含已脱敏字段，禁止整文件覆盖；请改用业务专用配置工具。"
    preview = _build_preview(snapshot, new_content)
    if preview["diff_truncated"]:
        return "完整差异超过审批展示上限，禁止通过通用工具写入；请改用业务专用配置工具。"
    return json.dumps(preview, ensure_ascii=False, indent=2)


@tool(args_schema=UpdateServerFileInput)
async def update_server_file(
    server_name: str,
    path: str,
    new_content: str,
    expected_sha256: str,
    preview_hash: str,
    preview_diff: str,
) -> str:
    """审批后更新精确授权的服务器文本文件。

    这是高风险写操作。执行前重新读取文件并核对旧哈希、预览哈希和差异；随后上传到固定暂存目录，
    由 root 网关备份、保留所有权与权限并原子替换。不能写入敏感凭据或任意未授权路径。
    """
    content_error = _validate_new_content(new_content)
    if content_error:
        return f"已拒绝写入：{content_error}。"
    server, error = await _resolve_server(server_name)
    if server is None:
        return error or "服务器不存在"
    snapshot, read_error = await _read_snapshot(server, path)
    if snapshot is None:
        return read_error or "读取失败"
    if snapshot.redacted:
        return "当前文件包含敏感字段，禁止通过通用工具整文件覆盖。"
    current_preview = _build_preview(snapshot, new_content)
    if not current_preview["changed"]:
        return "新旧内容相同，没有修改服务器。"
    if current_preview["diff_truncated"]:
        return "完整差异超过审批展示上限，已拒绝通过通用工具写入。"
    if (
        snapshot.sha256 != expected_sha256
        or current_preview["preview_hash"] != preview_hash
        or current_preview["preview_diff"] != preview_diff
    ):
        return "文件内容或审批差异已变化，请重新生成预览并再次确认。"

    params, connect_error = _connect_params_or_error(server)
    if params is None:
        return connect_error or "SSH 配置不可用"
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
        return f"连接、上传或更新目标服务器文件失败：{exc}"
    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.exit_status != 0:
        return f"更新文件失败（exit={result.exit_status}）：{output or '无输出'}"
    return output or f"已更新 {snapshot.path}。"
