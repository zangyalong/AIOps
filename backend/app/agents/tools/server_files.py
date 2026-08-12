"""通过目标服务器固定网关查找、脱敏读取和受控更新文本配置。"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import shlex
from dataclasses import dataclass
from typing import Literal

import asyncssh
from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from app.db.session import AsyncSessionLocal
from app.models.server import Server
from app.operations.executors import server_file as server_file_executor
from app.operations.schemas import OperationResult
from app.services import server_service
from app.services.ssh_service import (
    SSHConnectParams,
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

FILE_READ_GATEWAY = "/usr/local/sbin/aioms-file-read"
MAX_FILE_BYTES = 65_536
MAX_DIFF_CHARS = 12_000
MAX_CONFIG_VALUE_BYTES = 4_096
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@*?-]{1,100}$")
_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SENSITIVE_SELECTOR = re.compile(
    r"password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|credential|authorization",
    re.IGNORECASE,
)
_SENSITIVE_CONTENT = re.compile(
    r"(?im)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
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

    @field_validator("preview_diff")
    @classmethod
    def normalize_preview_diff(cls, value: str) -> str:
        return _normalize_preview_diff(value)


ConfigFormat = Literal["dotenv", "json", "yaml"]
ConfigScalar = str | int | float | bool | None


class PreviewServerConfigPatchInput(ReadServerFileInput):
    config_format: ConfigFormat
    selector: str = Field(min_length=1, max_length=1024)
    value: ConfigScalar

    @field_validator("selector")
    @classmethod
    def clean_selector(cls, value: str) -> str:
        value = value.strip()
        if any(char in value for char in ("\x00", "\n", "\r")):
            raise ValueError("选择器不能包含空字符或换行")
        return value


class PatchServerConfigInput(PreviewServerConfigPatchInput):
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

    @field_validator("preview_diff")
    @classmethod
    def normalize_preview_diff(cls, value: str) -> str:
        return _normalize_preview_diff(value)


@dataclass(frozen=True)
class RemoteFileSnapshot:
    path: str
    sha256: str
    redacted: bool
    content: str


@dataclass(frozen=True)
class ConfigPatchPreview:
    path: str
    config_format: ConfigFormat
    selector: str
    line: int | None
    before: ConfigScalar
    after: ConfigScalar
    expected_sha256: str
    output_sha256: str
    preview_hash: str
    preview_diff: str
    changed: bool


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
        return (
            "新内容疑似包含密码、Token、密钥或带密码的连接串，禁止进入 Agent 工具参数"
        )
    return None


def _config_value_json(value: ConfigScalar) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _normalize_preview_diff(value: str) -> str:
    """统一模型复制时容易丢失的行尾表示，保留每一行的实际差异内容。"""
    return value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _validate_config_patch(
    path: str,
    config_format: ConfigFormat,
    selector: str,
    value: ConfigScalar,
) -> str | None:
    filename = path.rsplit("/", 1)[-1]
    if config_format == "dotenv":
        if not (
            filename == ".env"
            or filename.startswith(".env.")
            or filename.endswith(".env")
        ):
            return "dotenv 驱动只允许 .env、.env.* 或 *.env 文件"
        if not _ENV_KEY_PATTERN.fullmatch(selector):
            return "dotenv 选择器必须是合法环境变量名"
        selector_parts = [selector]
        if not isinstance(value, str):
            return "dotenv 新值必须是字符串"
    elif config_format in {"json", "yaml"}:
        format_label = "JSON" if config_format == "json" else "YAML"
        if config_format == "json":
            allowed_suffixes = (".json",)
            allowed_suffixes_text = ".json"
        else:
            allowed_suffixes = (".yaml", ".yml")
            allowed_suffixes_text = ".yaml 或 .yml"
        if not filename.endswith(allowed_suffixes):
            return f"{format_label} 驱动只允许 {allowed_suffixes_text} 文件"
        if not selector.startswith("/"):
            return f"{format_label} 选择器必须是非空 JSON Pointer，例如 /server/port"
        raw_parts = selector[1:].split("/")
        if any(re.search(r"~(?:[^01]|$)", part) for part in raw_parts):
            return "JSON Pointer 中 ~ 只能写成 ~0 或 ~1"
        selector_parts = [
            part.replace("~1", "/").replace("~0", "~") for part in raw_parts
        ]
    else:
        return "config_format 只支持 dotenv、json 或 yaml"
    sensitive = next(
        (part for part in selector_parts if _SENSITIVE_SELECTOR.search(part)), None
    )
    if sensitive is not None:
        return f"选择器包含敏感字段 {sensitive}，禁止通过 Agent 修改"
    if isinstance(value, float) and not math.isfinite(value):
        return "数字必须是有限值"
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_CONFIG_VALUE_BYTES:
            return f"字符串值超过 {MAX_CONFIG_VALUE_BYTES} 字节限制"
        if any(char in value for char in ("\x00", "\n", "\r")):
            return "字符串值不能包含空字符或换行"
        content_error = _validate_new_content(f"value={value}\n")
        if content_error:
            return content_error
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


def _same_scalar(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _config_diff(
    config_format: ConfigFormat,
    selector: str,
    before: ConfigScalar,
    after: ConfigScalar,
    line: int | None,
) -> str:
    header = (
        f"@@ -{line},1 +{line},1 @@ {config_format} {selector}"
        if line is not None
        else f"@@ {config_format} {selector} @@"
    )
    if config_format == "dotenv":
        return _normalize_preview_diff(
            f"{header}\n-{selector}={before}\n+{selector}={after}\n"
        )
    return _normalize_preview_diff(
        f"{header}\n-{_config_value_json(before)}\n+{_config_value_json(after)}\n"
    )


async def _preview_config_patch(
    server: Server,
    path: str,
    config_format: ConfigFormat,
    selector: str,
    value: ConfigScalar,
) -> tuple[ConfigPatchPreview | None, str | None]:
    value_json = _config_value_json(value)
    status, output = await _run_read_gateway(
        server,
        "config-preview",
        path,
        config_format,
        selector,
        value_json,
    )
    if status != 0:
        return None, output or f"结构化配置预览失败（exit={status}）"
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None, "目标服务器配置补丁网关返回了无法识别的结果"
    if not isinstance(payload, dict):
        return None, "目标服务器配置补丁网关返回的结果不是对象"
    expected_sha256 = payload.get("source_sha256")
    output_sha256 = payload.get("output_sha256")
    before = payload.get("before")
    after = payload.get("after")
    changed = payload.get("changed")
    line = payload.get("line")
    if (
        payload.get("format") != config_format
        or payload.get("selector") != selector
        or not _SHA256_PATTERN.fullmatch(str(expected_sha256 or ""))
        or not _SHA256_PATTERN.fullmatch(str(output_sha256 or ""))
        or not isinstance(changed, bool)
        or (
            line is not None
            and (isinstance(line, bool) or not isinstance(line, int) or line < 1)
        )
        or not _same_scalar(after, value)
        or isinstance(before, (dict, list))
    ):
        return None, "目标服务器配置补丁网关返回的字段不完整或与请求不一致"
    preview_diff = (
        _config_diff(config_format, selector, before, after, line)
        if changed
        else "（配置值没有变化）"
    )
    preview_hash = hashlib.sha256(
        json.dumps(
            {
                "path": path,
                "format": config_format,
                "selector": selector,
                "line": line,
                "before": before,
                "after": after,
                "expected_sha256": expected_sha256,
                "output_sha256": output_sha256,
                "preview_diff": preview_diff,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        ConfigPatchPreview(
            path=path,
            config_format=config_format,
            selector=selector,
            line=line,
            before=before,
            after=after,
            expected_sha256=str(expected_sha256),
            output_sha256=str(output_sha256),
            preview_hash=preview_hash,
            preview_diff=preview_diff,
            changed=changed,
        ),
        None,
    )


def _build_preview(snapshot: RemoteFileSnapshot, new_content: str) -> dict[str, object]:
    diff = "".join(
        difflib.unified_diff(
            snapshot.content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"{snapshot.path}（当前）",
            tofile=f"{snapshot.path}（拟更新）",
        )
    )
    diff = _normalize_preview_diff(diff)
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
        return (
            "当前文件包含已脱敏字段，禁止整文件覆盖；如果只修改 dotenv、JSON 或 YAML "
            "的已有单个非敏感标量，请改用 change.query=config-patch-preview。"
        )
    preview = _build_preview(snapshot, new_content)
    if preview["diff_truncated"]:
        return (
            "完整差异超过审批展示上限，禁止通过通用工具写入；请改用业务专用配置工具。"
        )
    return json.dumps(preview, ensure_ascii=False, indent=2)


@tool(args_schema=PreviewServerConfigPatchInput)
async def preview_server_config_patch(
    server_name: str,
    path: str,
    config_format: ConfigFormat,
    selector: str,
    value: ConfigScalar,
) -> str:
    """预览已授权配置文件中的一个非敏感标量补丁，不返回其他真实配置值。

    dotenv 使用环境变量名作为 selector；JSON/YAML 使用 JSON Pointer（例如
    /services/app/ports/0）。只修改已存在且唯一的字段，不允许密码、Token、密钥、
    新增字段、对象或数组。成功返回的三个审批字段必须原样交给 patch_server_config。
    """
    validation_error = _validate_config_patch(path, config_format, selector, value)
    if validation_error:
        return f"无法生成结构化配置预览：{validation_error}。"
    server, error = await _resolve_server(server_name)
    if server is None:
        return error or "服务器不存在"
    preview, preview_error = await _preview_config_patch(
        server, path, config_format, selector, value
    )
    if preview is None:
        return f"无法生成结构化配置预览：{preview_error or '配置状态不明确'}。"
    return json.dumps(
        {
            "path": preview.path,
            "config_format": preview.config_format,
            "selector": preview.selector,
            "expected_sha256": preview.expected_sha256,
            "preview_hash": preview.preview_hash,
            "preview_diff": preview.preview_diff,
            "changed": preview.changed,
            "notice": (
                "目标机只修改该非敏感标量；其他字段、密码和密钥不会返回给模型，"
                "也不会被脱敏占位符覆盖。"
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


@tool(args_schema=UpdateServerFileInput)
async def update_server_file(
    server_name: str,
    path: str,
    new_content: str,
    expected_sha256: str,
    preview_hash: str,
    preview_diff: str,
) -> OperationResult:
    """审批后更新精确授权的服务器文本文件。

    这是高风险写操作。执行前重新读取文件并核对旧哈希、预览哈希和差异；随后上传到固定暂存目录，
    由 root 网关备份、保留所有权与权限并原子替换。不能写入敏感凭据或任意未授权路径。
    """
    return await server_file_executor.update_server_file(
        server_name,
        path,
        new_content,
        expected_sha256,
        preview_hash,
        preview_diff,
        validate_content=_validate_new_content,
        resolve_server=_resolve_server,
        read_snapshot=_read_snapshot,
        build_preview=_build_preview,
        connect_params=_connect_params_or_error,
    )


@tool(args_schema=PatchServerConfigInput)
async def patch_server_config(
    server_name: str,
    path: str,
    config_format: ConfigFormat,
    selector: str,
    value: ConfigScalar,
    expected_sha256: str,
    preview_hash: str,
    preview_diff: str,
) -> OperationResult:
    """审批后原子修改已授权 dotenv、JSON 或 YAML 文件中的一个非敏感标量。

    执行前会在目标机重新生成同一补丁并核对旧文件哈希与审批差异；root 网关备份旧文件、保留
    所有权和权限后原子替换。真实密钥始终留在目标机。
    """
    return await server_file_executor.patch_server_config(
        server_name,
        path,
        config_format,
        selector,
        value,
        expected_sha256,
        preview_hash,
        preview_diff,
        validate_patch=_validate_config_patch,
        resolve_server=_resolve_server,
        preview_patch=_preview_config_patch,
        encode_value=_config_value_json,
        connect_params=_connect_params_or_error,
    )
