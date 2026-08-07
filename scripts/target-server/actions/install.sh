#!/bin/bash

set -euo pipefail

fail() {
    echo "actions/install.sh: $*" >&2
    exit 1
}

usage() {
    echo "用法：sudo ./actions/install.sh --ssh-user USER --allowlist systemd:a.service,docker:container"
}

SSH_USER=""
ALLOWLIST=""
while (($# > 0)); do
    case "$1" in
        --ssh-user)
            (($# >= 2)) || fail "--ssh-user 缺少参数"
            SSH_USER="$2"
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
[[ "$SSH_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "AIOMS SSH 用户格式不合法"
[[ "$SSH_USER" != "root" ]] || fail "AIOMS SSH 用户不能是 root"
id "$SSH_USER" >/dev/null 2>&1 || fail "AIOMS SSH 用户不存在：$SSH_USER"
command -v visudo >/dev/null 2>&1 || fail "找不到 visudo"

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly GATEWAY_SOURCE="$SCRIPT_DIR/aioms-service-control"
readonly POLICY_SOURCE="$SCRIPT_DIR/aioms-service-policy"
readonly SUDOERS_TEMPLATE="$SCRIPT_DIR/aioms-service-control.sudoers"
[[ -f "$GATEWAY_SOURCE" ]] || fail "缺少固定服务控制包装器"
[[ -f "$POLICY_SOURCE" ]] || fail "缺少固定服务授权策略包装器"
[[ -f "$SUDOERS_TEMPLATE" ]] || fail "缺少 sudoers 模板"

allowlist_tmp="$(mktemp)"
sudoers_tmp="$(mktemp)"
cleanup() {
    rm -f -- "$allowlist_tmp" "$sudoers_tmp"
}
trap cleanup EXIT

IFS=',' read -r -a targets <<<"$ALLOWLIST"
for raw in "${targets[@]}"; do
    target="${raw#"${raw%%[![:space:]]*}"}"
    target="${target%"${target##*[![:space:]]}"}"
    [[ -z "$target" ]] && continue
    if [[ "$target" =~ ^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$ ]]; then
        target="systemd:$target"
    fi
    if [[ "$target" =~ ^systemd:(.+)$ ]]; then
        [[ "${BASH_REMATCH[1]}" =~ ^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$ ]] ||
            fail "systemd 白名单格式不合法：$target"
    elif [[ "$target" =~ ^docker:(.+)$ ]]; then
        [[ "${BASH_REMATCH[1]}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] ||
            fail "Docker 白名单格式不合法：$target"
    else
        fail "白名单运行时身份格式不合法：$target"
    fi
    printf '%s\n' "$target" >>"$allowlist_tmp"
done
sort -u -o "$allowlist_tmp" "$allowlist_tmp"

install -d -o root -g root -m 0755 /etc/aioms
install -o root -g root -m 0755 "$GATEWAY_SOURCE" /usr/local/sbin/aioms-service-control
install -o root -g root -m 0755 "$POLICY_SOURCE" /usr/local/sbin/aioms-service-policy
install -o root -g root -m 0644 "$allowlist_tmp" /etc/aioms/service-control.allowlist

sed "s/__AIOMS_SSH_USER__/$SSH_USER/g" "$SUDOERS_TEMPLATE" >"$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
install -o root -g root -m 0440 "$sudoers_tmp" /etc/sudoers.d/aioms-service-control

echo "统一服务控制网关已安装；允许运行对象数：$(wc -l < /etc/aioms/service-control.allowlist)"
