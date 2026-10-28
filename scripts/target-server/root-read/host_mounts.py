#!/usr/bin/python3.12
"""从 Linux mountinfo 输出去重后的挂载点与文件系统类型，使用 NUL 分隔。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MOUNTINFO_PATH = Path("/proc/self/mountinfo")
_OCTAL_ESCAPE = re.compile(rb"\\([0-7]{3})")


def _unescape_mount_field(value: bytes) -> bytes:
    return _OCTAL_ESCAPE.sub(
        lambda match: bytes((int(match.group(1), 8),)),
        value,
    )


def _parse_mountinfo(raw: bytes) -> list[tuple[bytes, bytes]]:
    """解析 mountinfo；同一目标存在堆叠挂载时只保留最后出现的一层。"""
    mounts: dict[bytes, bytes] = {}
    for line in raw.splitlines():
        left, separator, right = line.partition(b" - ")
        if not separator:
            continue
        left_fields = left.split()
        right_fields = right.split()
        if len(left_fields) < 5 or not right_fields:
            continue
        target = _unescape_mount_field(left_fields[4])
        filesystem_type = right_fields[0]
        if target.startswith(b"/") and b"\x00" not in target:
            mounts[target] = filesystem_type
    return sorted(
        mounts.items(),
        key=lambda item: (item[0].count(b"/"), item[0]),
    )


def main() -> None:
    for target, filesystem_type in _parse_mountinfo(MOUNTINFO_PATH.read_bytes()):
        sys.stdout.buffer.write(target)
        sys.stdout.buffer.write(b"\x00")
        sys.stdout.buffer.write(filesystem_type)
        sys.stdout.buffer.write(b"\x00")


if __name__ == "__main__":
    main()
