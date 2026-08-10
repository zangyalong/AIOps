#!/usr/bin/python3
"""AIOMS root 只读容器入口：校验 stdin 协议并在容器内执行诊断命令。"""

from __future__ import annotations

import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from typing import NoReturn

MAX_REQUEST_BYTES = 16_384
MAX_COMMAND_CHARS = 8_192
MAX_OUTPUT_BYTES = 65_536
MAX_TIMEOUT_SECONDS = 20

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


def _emit(*, exit_code: int, output: str, timed_out: bool, truncated: bool) -> None:
    payload = {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "truncated": truncated,
        "output": _redact(output),
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _fail(message: str, *, exit_code: int = 126) -> NoReturn:
    _emit(
        exit_code=exit_code,
        output=f"root-read 请求被拒绝：{message}",
        timed_out=False,
        truncated=False,
    )
    raise SystemExit(exit_code)


def _redact(value: str) -> str:
    value = _PRIVATE_KEY_BLOCK.sub("[已屏蔽私钥内容]", value)
    value = _URL_CREDENTIALS.sub(r"\1[已脱敏]\3", value)
    value = _AUTHORIZATION.sub(r"\1[已脱敏]", value)
    return _SECRET_ASSIGNMENT.sub(r"\1[已脱敏]", value)


def _load_request() -> tuple[str, int]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        _fail(f"请求不能超过 {MAX_REQUEST_BYTES} 字节")
    if not raw:
        _fail("stdin 为空")

    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("stdin 必须是有效 UTF-8 JSON")

    if not isinstance(request, dict):
        _fail("请求必须是 JSON 对象")
    if set(request) - {"command", "timeout"}:
        _fail("请求包含不支持的字段")

    command = request.get("command")
    if not isinstance(command, str) or not command.strip():
        _fail("command 必须是非空字符串")
    if len(command) > MAX_COMMAND_CHARS:
        _fail(f"command 不能超过 {MAX_COMMAND_CHARS} 个字符")
    if _CONTROL_CHARACTER.search(command):
        _fail("command 包含异常控制字符")

    timeout = request.get("timeout", MAX_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        _fail("timeout 必须是整数秒")
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        _fail(f"timeout 必须在 1 到 {MAX_TIMEOUT_SECONDS} 秒之间")

    return command, timeout


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=0.5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _run(command: str, timeout: int) -> tuple[int, str, bool, bool]:
    environment = {
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PAGER": "cat",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "SYSTEMD_PAGER": "cat",
    }
    process = subprocess.Popen(
        ["/bin/bash", "--noprofile", "--norc", "-o", "pipefail", "-c", command],
        cwd="/tmp",
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdout is not None

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    chunks = bytearray()
    timed_out = False
    truncated = False

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate(process)
                break

            for key, _ in selector.select(timeout=min(0.1, remaining)):
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue

                available = MAX_OUTPUT_BYTES - len(chunks)
                if len(chunk) > available:
                    chunks.extend(chunk[:available])
                    truncated = True
                    _terminate(process)
                    break
                chunks.extend(chunk)

            if truncated:
                break
            if process.poll() is not None and not selector.get_map():
                break
    finally:
        selector.close()
        if process.poll() is None:
            _terminate(process)
        process.stdout.close()

    if timed_out:
        exit_code = 124
    elif truncated:
        exit_code = 125
    else:
        exit_code = process.wait()

    output = chunks.decode("utf-8", errors="replace")
    if timed_out:
        output += f"\n[AIOMS：命令超过 {timeout} 秒，已终止]"
    elif truncated:
        output += f"\n[AIOMS：输出超过 {MAX_OUTPUT_BYTES} 字节，已截断并终止命令]"
    return exit_code, output, timed_out, truncated


def main() -> None:
    command, timeout = _load_request()
    exit_code, output, timed_out, truncated = _run(command, timeout)
    _emit(
        exit_code=exit_code,
        output=output,
        timed_out=timed_out,
        truncated=truncated,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
