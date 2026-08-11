"""通过目标服务器固定入口运行通用 root 只读诊断命令。"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import asyncssh
from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from app.db.session import AsyncSessionLocal
from app.operations.schemas import OperationResult
from app.services import server_service
from app.services.ssh_service import (
    SSHKeyMissing,
    SSHNotConfigured,
    build_connect_params,
)

ROOT_READ_GATEWAY = "/usr/local/sbin/aioms-root-read"
ROOT_READ_REMOTE_COMMAND = f"sudo -n -- {ROOT_READ_GATEWAY}"
ROOT_READ_TIMEOUT_SECONDS = 20
SSH_TIMEOUT_SECONDS = 35
MAX_COMMAND_CHARS = 8_192
MAX_OUTPUT_CHARS = 12_000
ROOT_READ_TOOLBOX_NOTICE = (
    "[执行语境] Shell、PATH、程序版本及未加 /host 前缀的路径都属于 AIOMS "
    "诊断容器，不属于目标服务器；宿主机文件根是 /host。看到 AIOMS_PATH_MASKED "
    "表示路径已被安全遮蔽，不能判断为空或不存在。"
)
ROOT_READ_HOST_NOTICE = (
    "[证据范围] 命令的 /、PATH 和可执行程序均来自目标服务器；宿主文件系统只读，"
    "敏感路径和运行时控制面已遮蔽。普通 Unix 所有者及 700/777 等权限位不能用来判定 "
    "AIOMS 是否获准读取，也不能扩大到目录内其他文件；只有实际出现 AIOMS_PATH_MASKED、权限错误或不存在"
    "才能判定无法读取。即使权限位允许写，本次调查也不可写。"
)

RootReadScope = Literal["host", "toolbox"]

_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret)\b[\"']?\s*[:=]\s*)"
    r"([^,;}\r\n]+)"
)
_URL_CREDENTIALS = re.compile(
    r"([a-z][a-z0-9+.-]*://[^:/\s]+:)([^@/\s]+)(@)", re.IGNORECASE
)
_AUTHORIZATION = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)\b\s*[:=]\s*)([^\r\n]+)"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
    r"-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)


class RootReadInput(BaseModel):
    server_name: str = Field(min_length=1, max_length=100)
    command: str = Field(min_length=1, max_length=MAX_COMMAND_CHARS)

    @field_validator("server_name", "command")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("command")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if _CONTROL_CHARACTER.search(value):
            raise ValueError("命令包含 NUL、回车或异常控制字符")
        return value


def _redact(value: str) -> str:
    value = _PRIVATE_KEY_BLOCK.sub("[已屏蔽私钥内容]", value)
    value = _URL_CREDENTIALS.sub(r"\1[已脱敏]\3", value)
    value = _AUTHORIZATION.sub(r"\1[已脱敏]", value)
    return _SECRET_ASSIGNMENT.sub(r"\1[已脱敏]", value)


def _failure(
    message: str,
    *,
    code: str,
    exit_code: int | None = None,
    timed_out: bool = False,
) -> OperationResult:
    return OperationResult.failure(
        _redact(message),
        code=code,
        metadata={"exit_code": exit_code, "timed_out": timed_out},
    )


async def execute_root_read(
    server_name: str,
    command: str,
    *,
    scope: RootReadScope,
) -> OperationResult:
    """调用固定 root-read 网关；执行语境只由后端选择，不接受模型透传。"""
    try:
        parsed = RootReadInput(server_name=server_name, command=command)
    except ValueError as exc:
        return _failure(str(exc), code="invalid_request")

    async with AsyncSessionLocal() as db:
        server = await server_service.get_server_by_name_or_ip(db, parsed.server_name)
    if server is None:
        return _failure(
            f"没有找到名称或 IP 为 {parsed.server_name} 的服务器，请先确认服务器。",
            code="server_not_found",
        )

    try:
        params = build_connect_params(server)
    except SSHNotConfigured:
        return _failure(
            f"{parsed.server_name} 没有配置 AIOMS SSH 身份，无法执行宿主机只读调查。",
            code="ssh_not_configured",
        )
    except SSHKeyMissing as exc:
        return _failure(
            f"{exc}，请确认 AIOMS 私钥位置。",
            code="ssh_key_missing",
        )

    request = json.dumps(
        {
            "command": parsed.command,
            "timeout": ROOT_READ_TIMEOUT_SECONDS,
            "scope": scope,
        },
        ensure_ascii=False,
        separators=(",", ":"),
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
            result = await conn.run(
                ROOT_READ_REMOTE_COMMAND,
                input=request,
                check=False,
                timeout=SSH_TIMEOUT_SECONDS,
            )
    except TimeoutError:
        return _failure(
            f"{parsed.server_name} 的宿主机只读调查超过 {SSH_TIMEOUT_SECONDS} 秒，已停止等待。",
            code="ssh_timeout",
            timed_out=True,
        )
    except (asyncssh.Error, OSError) as exc:
        return _failure(
            f"连接或调用 {parsed.server_name} 的宿主机只读调查失败：{exc}",
            code="ssh_failed",
        )

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    try:
        payload: Any = json.loads(stdout)
    except json.JSONDecodeError:
        detail = _redact(f"{stdout}{stderr}".strip()) or "远端没有返回内容"
        return _failure(
            f"{parsed.server_name} 的 root 只读网关返回了无效协议：{detail[:1000]}",
            code="invalid_gateway_response",
            exit_code=result.exit_status,
        )

    if not isinstance(payload, dict):
        return _failure(
            f"{parsed.server_name} 的 root 只读网关返回值不是 JSON 对象。",
            code="invalid_gateway_response",
            exit_code=result.exit_status,
        )

    exit_code = payload.get("exit_code")
    timed_out = payload.get("timed_out")
    truncated = payload.get("truncated")
    output = payload.get("output")
    if (
        isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or not isinstance(timed_out, bool)
        or not isinstance(truncated, bool)
        or not isinstance(output, str)
    ):
        return _failure(
            f"{parsed.server_name} 的 root 只读网关响应字段不完整。",
            code="invalid_gateway_response",
            exit_code=result.exit_status,
        )

    output = _redact(output).strip() or "（命令没有输出）"
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n…（后端再次截断）"
        truncated = True
    scope_notice = (
        ROOT_READ_HOST_NOTICE if scope == "host" else ROOT_READ_TOOLBOX_NOTICE
    )
    source_label = "宿主机只读调查" if scope == "host" else "文件调查工具箱"
    message = (
        f"（{source_label}：{parsed.server_name}；退出码 {exit_code}；"
        f"超时={'是' if timed_out else '否'}；截断={'是' if truncated else '否'}）"
        f"\n{scope_notice}\n$ {parsed.command}\n{output}"
    )
    metadata = {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "truncated": truncated,
        "source": "host_read" if scope == "host" else "file_toolbox",
    }
    if exit_code == 0 and not timed_out:
        return OperationResult.success(
            message,
            code="root_read_completed",
            metadata=metadata,
        )
    return OperationResult.failure(
        message,
        code="root_read_failed",
        metadata=metadata,
    )


async def inspect_host(server_name: str, command: str) -> OperationResult:
    """在宿主机真实根目录和 PATH 下执行内核强制只读的通用调查。"""
    return await execute_root_read(server_name, command, scope="host")


@tool(args_schema=RootReadInput)
async def run_root_read_command(server_name: str, command: str) -> OperationResult:
    """兼容旧调用的文件工具箱入口；新 Agent 不直接使用本工具。"""
    return await execute_root_read(server_name, command, scope="toolbox")
