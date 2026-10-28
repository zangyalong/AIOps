#!/bin/bash

set -euo pipefail

fail() {
    echo "install.sh: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ $# -eq 1 ]] || fail "用法：./install.sh <AIOps SSH 用户名>"

readonly AIOPS_SSH_USER="$1"
readonly USER_PATTERN='^[a-z_][a-z0-9_-]*[$]?$'
[[ "$AIOPS_SSH_USER" =~ $USER_PATTERN ]] || fail "SSH 用户名格式不合法"
id "$AIOPS_SSH_USER" >/dev/null 2>&1 || fail "用户 $AIOPS_SSH_USER 不存在"

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SUDOERS_TARGET="/etc/sudoers.d/aiops-readonly"
readonly TEMP_SUDOERS="$(mktemp /etc/sudoers.d/.aiops-readonly.XXXXXX)"

cleanup() {
    rm -f -- "$TEMP_SUDOERS"
}
trap cleanup EXIT

command -v install >/dev/null 2>&1 || fail "系统缺少 install 命令"
command -v visudo >/dev/null 2>&1 || fail "系统缺少 visudo 命令"

install -o root -g root -m 0755 \
    "$SCRIPT_DIR/aiops-docker-read" \
    /usr/local/sbin/aiops-docker-read
install -o root -g root -m 0755 \
    "$SCRIPT_DIR/aiops-service-read" \
    /usr/local/sbin/aiops-service-read

sed "s/@AIOPS_SSH_USER@/$AIOPS_SSH_USER/g" \
    "$SCRIPT_DIR/aiops-readonly.sudoers" >"$TEMP_SUDOERS"
chmod 0440 "$TEMP_SUDOERS"
visudo -cf "$TEMP_SUDOERS"
install -o root -g root -m 0440 "$TEMP_SUDOERS" "$SUDOERS_TARGET"
visudo -cf "$SUDOERS_TARGET"

echo "AIOps 目标服务器只读网关安装完成。"
echo "SSH 用户：$AIOPS_SSH_USER"
echo "sudoers：$SUDOERS_TARGET"
echo "请按照 README.md 的“安装后验证”逐项检查。"
