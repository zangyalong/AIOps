#!/bin/bash

set -euo pipefail

fail() {
    echo "files/install.sh: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ $# -eq 1 ]] || fail "用法：sudo ./files/install.sh AIOMS_SSH_USER"
readonly AIOMS_SSH_USER="$1"
[[ "$AIOMS_SSH_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ && "$AIOMS_SSH_USER" != "root" ]] ||
    fail "AIOMS SSH 用户格式不合法"
id "$AIOMS_SSH_USER" >/dev/null 2>&1 || fail "AIOMS SSH 用户不存在"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

install -d -o root -g root -m 0755 /usr/local/libexec/aioms /etc/aioms
install -o root -g root -m 0644 "$SCRIPT_DIR/aioms-file-common" /usr/local/libexec/aioms/file-common
readonly HOST_PYTHON="/usr/bin/python3.11"
[[ -x "$HOST_PYTHON" ]] || fail "系统缺少 Python 3.11：请先安装 python3.11"
"$HOST_PYTHON" -c \
    'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' \
    "$SCRIPT_DIR/config_patch.py" \
    || fail "结构化配置补丁器无法由 Python 3.11 解析"
install -o root -g root -m 0555 \
    "$SCRIPT_DIR/config_patch.py" /usr/local/libexec/aioms/config-patch.py
for gateway in aioms-file-read aioms-file-policy aioms-file-write; do
    install -o root -g root -m 0755 "$SCRIPT_DIR/$gateway" "/usr/local/sbin/$gateway"
done
if [[ ! -f /etc/aioms/file-access.allowlist ]]; then
    install -o root -g root -m 0644 /dev/null /etc/aioms/file-access.allowlist
else
    chown root:root /etc/aioms/file-access.allowlist
    chmod 0644 /etc/aioms/file-access.allowlist
fi
group="$(id -gn "$AIOMS_SSH_USER")"
install -d -o "$AIOMS_SSH_USER" -g "$group" -m 0700 /var/lib/aioms/file-incoming
install -d -o root -g root -m 0700 /var/lib/aioms/file-backups

tmp="$(mktemp /etc/sudoers.d/.aioms-files.XXXXXX)"
trap 'rm -f -- "$tmp"' EXIT
sed "s/__AIOMS_SSH_USER__/$AIOMS_SSH_USER/g" "$SCRIPT_DIR/aioms-files.sudoers" >"$tmp"
chmod 0440 "$tmp"
visudo -cf "$tmp" >/dev/null
install -o root -g root -m 0440 "$tmp" /etc/sudoers.d/aioms-files
visudo -cf /etc/sudoers.d/aioms-files >/dev/null
echo "AIOMS 文件访问网关已安装；默认没有任何文件授权。"
