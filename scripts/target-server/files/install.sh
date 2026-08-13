#!/bin/bash

set -euo pipefail

fail() {
    echo "files/install.sh: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ $# -eq 1 ]] || fail "用法：sudo ./files/install.sh AIOPS_SSH_USER"
readonly AIOPS_SSH_USER="$1"
[[ "$AIOPS_SSH_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ && "$AIOPS_SSH_USER" != "root" ]] ||
    fail "AIOps SSH 用户格式不合法"
id "$AIOPS_SSH_USER" >/dev/null 2>&1 || fail "AIOps SSH 用户不存在"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

install -d -o root -g root -m 0755 /usr/local/libexec/aiops /etc/aiops
install -o root -g root -m 0644 "$SCRIPT_DIR/aiops-file-common" /usr/local/libexec/aiops/file-common
readonly HOST_PYTHON="/usr/bin/python3.11"
[[ -x "$HOST_PYTHON" ]] || fail "系统缺少 Python 3.11：请先安装 python3.11"
"$HOST_PYTHON" -c 'import yaml' >/dev/null 2>&1 ||
    fail "系统 Python 3.11 缺少 PyYAML：请先为 /usr/bin/python3.11 安装 PyYAML"
"$HOST_PYTHON" -c \
    'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' \
    "$SCRIPT_DIR/config_patch.py" \
    || fail "结构化配置补丁器无法由 Python 3.11 解析"
install -o root -g root -m 0555 \
    "$SCRIPT_DIR/config_patch.py" /usr/local/libexec/aiops/config-patch.py
for gateway in aiops-file-read aiops-file-policy aiops-file-write; do
    install -o root -g root -m 0755 "$SCRIPT_DIR/$gateway" "/usr/local/sbin/$gateway"
done
if [[ ! -f /etc/aiops/file-access.allowlist ]]; then
    install -o root -g root -m 0644 /dev/null /etc/aiops/file-access.allowlist
else
    chown root:root /etc/aiops/file-access.allowlist
    chmod 0644 /etc/aiops/file-access.allowlist
fi
group="$(id -gn "$AIOPS_SSH_USER")"
install -d -o "$AIOPS_SSH_USER" -g "$group" -m 0700 /var/lib/aiops/file-incoming
install -d -o root -g root -m 0700 /var/lib/aiops/file-backups

tmp="$(mktemp /etc/sudoers.d/.aiops-files.XXXXXX)"
trap 'rm -f -- "$tmp"' EXIT
sed "s/__AIOPS_SSH_USER__/$AIOPS_SSH_USER/g" "$SCRIPT_DIR/aiops-files.sudoers" >"$tmp"
chmod 0440 "$tmp"
visudo -cf "$tmp" >/dev/null
install -o root -g root -m 0440 "$tmp" /etc/sudoers.d/aiops-files
visudo -cf /etc/sudoers.d/aiops-files >/dev/null
echo "AIOps 文件访问网关已安装；默认没有任何文件授权。"
