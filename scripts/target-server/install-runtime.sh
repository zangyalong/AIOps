#!/bin/bash

set -euo pipefail

fail() {
    echo "install-runtime.sh: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ $# -eq 1 ]] || fail "用法：sudo ./install-runtime.sh /path/to/target-server"

readonly SOURCE_DIR="$(cd -- "$1" && pwd)"
readonly INSTALL_DIR="/opt/aioms-target-server"

[[ -x "$SOURCE_DIR/deploy.sh" ]] || fail "源目录缺少 deploy.sh"
[[ -f "$SOURCE_DIR/deploy.conf" ]] || fail "源目录缺少 deploy.conf"
[[ -x "$SOURCE_DIR/apply-incoming-config.sh" ]] ||
    fail "源目录缺少 apply-incoming-config.sh"

install -d -o root -g root -m 0755 "$INSTALL_DIR"
(
    cd "$SOURCE_DIR"
    tar -cf - .
) | (
    cd "$INSTALL_DIR"
    tar -xf -
)

chown -R root:root "$INSTALL_DIR"
chmod -R go-w "$INSTALL_DIR"
chmod 0600 "$INSTALL_DIR/deploy.conf"
for secret in \
    metrics/mysql-exporter.my.cnf \
    metrics/postgres-exporter.password \
    metrics/redis-exporter.password; do
    if [[ -f "$SOURCE_DIR/$secret" ]]; then
        chmod 0600 "$INSTALL_DIR/$secret"
    else
        rm -f -- "$INSTALL_DIR/$secret"
    fi
done

echo "AIOMS 固定部署运行包已安装：$INSTALL_DIR"
