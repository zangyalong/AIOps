#!/bin/bash

set -euo pipefail

fail() {
    echo "actions/install.sh: $*" >&2
    exit 1
}

usage() {
    echo "用法：sudo ./actions/install.sh --deploy-user USER --allowlist a.service,b.service"
}

DEPLOY_USER=""
ALLOWLIST=""
while (($# > 0)); do
    case "$1" in
        --deploy-user)
            (($# >= 2)) || fail "--deploy-user 缺少参数"
            DEPLOY_USER="$2"
            shift 2
            ;;
        --allowlist)
            (($# >= 2)) || fail "--allowlist 缺少参数"
            ALLOWLIST="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) fail "未知参数：$1" ;;
    esac
done

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ "$DEPLOY_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "部署用户格式不合法"
id "$DEPLOY_USER" >/dev/null 2>&1 || fail "部署用户不存在：$DEPLOY_USER"
command -v visudo >/dev/null 2>&1 || fail "找不到 visudo"

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly GATEWAY_SOURCE="$SCRIPT_DIR/aioms-service-control"
readonly SUDOERS_TEMPLATE="$SCRIPT_DIR/aioms-service-control.sudoers"
[[ -f "$GATEWAY_SOURCE" ]] || fail "缺少固定服务控制包装器"
[[ -f "$SUDOERS_TEMPLATE" ]] || fail "缺少 sudoers 模板"

allowlist_tmp="$(mktemp)"
sudoers_tmp="$(mktemp)"
cleanup() {
    rm -f -- "$allowlist_tmp" "$sudoers_tmp"
}
trap cleanup EXIT

IFS=',' read -r -a services <<<"$ALLOWLIST"
for raw in "${services[@]}"; do
    service="${raw#"${raw%%[![:space:]]*}"}"
    service="${service%"${service##*[![:space:]]}"}"
    [[ -z "$service" ]] && continue
    [[ "$service" =~ ^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$ ]] ||
        fail "白名单服务格式不合法：$service"
    printf '%s\n' "$service" >>"$allowlist_tmp"
done
sort -u -o "$allowlist_tmp" "$allowlist_tmp"

install -d -o root -g root -m 0755 /etc/aioms
install -o root -g root -m 0755 "$GATEWAY_SOURCE" /usr/local/sbin/aioms-service-control
install -o root -g root -m 0644 "$allowlist_tmp" /etc/aioms/service-control.allowlist

sed "s/__AIOMS_DEPLOY_USER__/$DEPLOY_USER/g" "$SUDOERS_TEMPLATE" >"$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
install -o root -g root -m 0440 "$sudoers_tmp" /etc/sudoers.d/aioms-service-control

echo "服务控制网关已安装；允许服务数：$(wc -l < /etc/aioms/service-control.allowlist)"
