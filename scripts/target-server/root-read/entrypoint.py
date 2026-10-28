#!/usr/local/bin/python3.12
"""AIOps root 只读容器入口：校验 stdin 协议并在容器内执行诊断命令。"""

from __future__ import annotations

import ctypes
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

_PR_CAP_AMBIENT = 47
_PR_CAP_AMBIENT_CLEAR_ALL = 4

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


def _load_request() -> tuple[str, int, str]:
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
    if set(request) - {"command", "timeout", "scope"}:
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

    scope = request.get("scope", "toolbox")
    if scope not in {"host", "toolbox"}:
        _fail("scope 只能是 host 或 toolbox")

    return command, timeout, scope


def _load_libcap() -> ctypes.CDLL | None:
    """在进入宿主机根目录前加载镜像自带的 libcap。"""
    # 单元测试会直接在 macOS 执行工具箱路径；真实入口只会运行在固定 Linux 镜像中。
    if sys.platform != "linux":
        return None
    try:
        libcap = ctypes.CDLL("libcap.so.2", use_errno=True)
    except OSError as exc:
        _fail(f"无法加载 libcap2：{exc}")
    libcap.cap_from_text.argtypes = [ctypes.c_char_p]
    libcap.cap_from_text.restype = ctypes.c_void_p
    libcap.cap_set_proc.argtypes = [ctypes.c_void_p]
    libcap.cap_set_proc.restype = ctypes.c_int
    libcap.cap_free.argtypes = [ctypes.c_void_p]
    libcap.cap_free.restype = ctypes.c_int
    return libcap


def _retain_read_capability_only(libcap: ctypes.CDLL | None) -> None:
    """移除进入宿主根所需的临时能力，只保留只读穿透权限。

    容器同时启用了 no_new_privileges，子进程无法通过 setuid 或文件 capability 把已经移除的
    SYS_CHROOT 重新取回。任意调查命令开始执行前都必须先完成此步骤。
    """
    if libcap is None:
        return

    capabilities = libcap.cap_from_text(b"cap_dac_read_search=ep")
    if not capabilities:
        errno = ctypes.get_errno()
        _fail(f"无法构造只读 capability 集合：errno={errno}")
    try:
        if libcap.cap_set_proc(capabilities) != 0:
            errno = ctypes.get_errno()
            _fail(f"无法收回临时 Linux capability：errno={errno}")
    finally:
        libcap.cap_free(capabilities)

    # Docker 默认不会设置 ambient capability；显式清空可防止运行环境变化后产生继承。
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_CAP_AMBIENT, _PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        _fail(f"无法清空 ambient capability：errno={errno}")


def _enter_execution_scope(scope: str) -> tuple[str, str]:
    # host scope chroot 后只能看到宿主文件，因此镜像依赖必须在这里提前装载。
    libcap = _load_libcap()
    if scope == "host":
        # chroot 本身不是安全边界；真正的边界来自外层容器 namespace、只读挂载、无网络、
        # 敏感路径遮蔽和 capability 收回。这里仅用于让绝对路径和动态链接器指向宿主机。
        try:
            os.chroot("/host")
            os.chdir("/")
        except OSError as exc:
            _fail(f"无法进入宿主机只读文件树：{exc}")
        _retain_read_capability_only(libcap)
        if os.path.exists("/bin/bash"):
            shell = "/bin/bash"
        elif os.path.exists("/bin/sh"):
            shell = "/bin/sh"
        else:
            _fail("目标服务器缺少 /bin/bash 和 /bin/sh")
        return shell, "/"

    _retain_read_capability_only(libcap)
    return "/bin/bash", "/tmp"


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


def _run(
    command: str,
    timeout: int,
    *,
    shell: str,
    cwd: str,
) -> tuple[int, str, bool, bool]:
    environment = {
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PAGER": "cat",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "SYSTEMD_PAGER": "cat",
    }
    shell_args = [shell]
    if shell.endswith("bash"):
        shell_args.extend(["--noprofile", "--norc", "-o", "pipefail"])
    shell_args.extend(["-c", command])
    process = subprocess.Popen(
        shell_args,
        cwd=cwd,
        env=environment,
        # stdin 使用匿名管道提供 EOF；host scope 另外只精确挂载
        # /dev/null，不暴露完整的宿主机 /dev。
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdin is not None
    process.stdin.close()
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
        output += f"\n[AIOps：命令超过 {timeout} 秒，已终止]"
    elif truncated:
        output += f"\n[AIOps：输出超过 {MAX_OUTPUT_BYTES} 字节，已截断并终止命令]"
    return exit_code, output, timed_out, truncated


def main() -> None:
    command, timeout, scope = _load_request()
    shell, cwd = _enter_execution_scope(scope)
    try:
        exit_code, output, timed_out, truncated = _run(
            command,
            timeout,
            shell=shell,
            cwd=cwd,
        )
    except OSError as exc:
        _fail(f"无法启动调查命令：{exc}")
    _emit(
        exit_code=exit_code,
        output=output,
        timed_out=timed_out,
        truncated=truncated,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
