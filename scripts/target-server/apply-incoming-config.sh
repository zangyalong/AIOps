#!/bin/bash

set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin

fail() {
    echo "apply-incoming-config.sh: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "只能由 root 固定部署网关调用"
[[ $# -eq 0 ]] || fail "不接受命令行参数"
[[ "${SUDO_UID:-}" =~ ^[1-9][0-9]*$ ]] || fail "缺少有效的 sudo 调用用户"

for required_command in cp dirname flock install mktemp rm stat; do
    command -v "$required_command" >/dev/null 2>&1 ||
        fail "目标服务器缺少命令：$required_command"
done

readonly INSTALL_DIR="/opt/aiops-target-server"
readonly DEPLOY_SCRIPT="$INSTALL_DIR/deploy.sh"
readonly CONFIG_PATH="$INSTALL_DIR/deploy.conf"
readonly INCOMING_DIR="/var/lib/aiops/incoming"
readonly INCOMING_CONFIG="$INCOMING_DIR/deploy.conf"
readonly INCOMING_MANIFEST="$INCOMING_DIR/deployment.manifest"
readonly LOCK_FILE="/run/lock/aiops-target-deploy.lock"
readonly MAX_CONFIG_SIZE=65536
readonly MAX_SECRET_SIZE=4096

verify_root_runtime() {
    local path="$1"
    local label="$2"
    local owner mode
    [[ -f "$path" && ! -L "$path" ]] || fail "$label 不存在或不是普通文件：$path"
    read -r owner mode < <(stat -c '%u %a' "$path")
    [[ "$owner" == "0" ]] || fail "$label 必须由 root 持有"
    (( (8#$mode & 8#022) == 0 )) || fail "$label 不能由组或其他用户写入"
}

verify_upload() {
    local path="$1"
    local label="$2"
    local max_size="$3"
    local owner mode size
    [[ -f "$path" && ! -L "$path" ]] || fail "$label 不存在或不是普通文件"
    read -r owner mode size < <(stat -c '%u %a %s' "$path")
    [[ "$owner" == "$SUDO_UID" ]] || fail "$label 必须由当前 AIOps SSH 用户上传"
    (( (8#$mode & 8#077) == 0 )) || fail "$label 不能允许 group/other 访问"
    ((size > 0 && size <= max_size)) || fail "$label 大小不合法"
}

verify_root_runtime "$DEPLOY_SCRIPT" "固定部署脚本"
verify_root_runtime "$INSTALL_DIR/apply-incoming-config.sh" "固定配置应用器"
verify_upload "$INCOMING_CONFIG" "部署配置" "$MAX_CONFIG_SIZE"
verify_upload "$INCOMING_MANIFEST" "部署清单" 1024

MYSQL_SECRET=""
POSTGRES_SECRET=""
REDIS_SECRET=""
SEEN_KEYS="|"
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" == *=* ]] || fail "部署清单必须使用 KEY=VALUE"
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$SEEN_KEYS" != *"|$key|"* ]] || fail "部署清单字段重复：$key"
    [[ "$value" == "replace" || "$value" == "preserve" || "$value" == "delete" ]] ||
        fail "部署清单动作不合法：$key=$value"
    case "$key" in
        MYSQL_SECRET) MYSQL_SECRET="$value" ;;
        POSTGRES_SECRET) POSTGRES_SECRET="$value" ;;
        REDIS_SECRET) REDIS_SECRET="$value" ;;
        *) fail "部署清单字段不受支持：$key" ;;
    esac
    SEEN_KEYS+="$key|"
done <"$INCOMING_MANIFEST"

for required in MYSQL_SECRET POSTGRES_SECRET REDIS_SECRET; do
    [[ -n "${!required}" ]] || fail "部署清单缺少字段：$required"
done

declare -A SECRET_TARGETS=(
    [MYSQL_SECRET]="$INSTALL_DIR/metrics/mysql-exporter.my.cnf"
    [POSTGRES_SECRET]="$INSTALL_DIR/metrics/postgres-exporter.password"
    [REDIS_SECRET]="$INSTALL_DIR/metrics/redis-exporter.password"
)
declare -A SECRET_UPLOADS=(
    [MYSQL_SECRET]="$INCOMING_DIR/mysql-exporter.my.cnf"
    [POSTGRES_SECRET]="$INCOMING_DIR/postgres-exporter.password"
    [REDIS_SECRET]="$INCOMING_DIR/redis-exporter.password"
)

for key in MYSQL_SECRET POSTGRES_SECRET REDIS_SECRET; do
    if [[ "${!key}" == "replace" ]]; then
        verify_upload "${SECRET_UPLOADS[$key]}" "$key" "$MAX_SECRET_SIZE"
    elif [[ "${!key}" == "preserve" ]]; then
        [[ -s "${SECRET_TARGETS[$key]}" ]] ||
            fail "$key 要求复用已有凭据，但目标服务器尚未安装该凭据"
    fi
done

exec 9>"$LOCK_FILE"
flock -x 9

backup_dir="$(mktemp -d /var/lib/aiops/.deployment-backup.XXXXXX)"
cleanup() {
    rm -rf -- "$backup_dir"
    rm -f -- \
        "$INCOMING_CONFIG" \
        "$INCOMING_MANIFEST" \
        "${SECRET_UPLOADS[MYSQL_SECRET]}" \
        "${SECRET_UPLOADS[POSTGRES_SECRET]}" \
        "${SECRET_UPLOADS[REDIS_SECRET]}"
}
trap cleanup EXIT

backup_file() {
    local source="$1"
    local name="$2"
    if [[ -f "$source" ]]; then
        cp -a -- "$source" "$backup_dir/$name"
    fi
}

backup_file "$CONFIG_PATH" deploy.conf
backup_file "${SECRET_TARGETS[MYSQL_SECRET]}" mysql-exporter.my.cnf
backup_file "${SECRET_TARGETS[POSTGRES_SECRET]}" postgres-exporter.password
backup_file "${SECRET_TARGETS[REDIS_SECRET]}" redis-exporter.password

install -o root -g root -m 0600 "$INCOMING_CONFIG" "$CONFIG_PATH"
for key in MYSQL_SECRET POSTGRES_SECRET REDIS_SECRET; do
    target="${SECRET_TARGETS[$key]}"
    case "${!key}" in
        replace)
            install -d -o root -g root -m 0755 "$(dirname -- "$target")"
            install -o root -g root -m 0600 "${SECRET_UPLOADS[$key]}" "$target"
            ;;
        delete) rm -f -- "$target" ;;
        preserve) ;;
    esac
done

restore_previous_files() {
    local target backup
    install -o root -g root -m 0600 "$backup_dir/deploy.conf" "$CONFIG_PATH"
    for name in mysql-exporter.my.cnf postgres-exporter.password redis-exporter.password; do
        target="$INSTALL_DIR/metrics/$name"
        backup="$backup_dir/$name"
        if [[ -f "$backup" ]]; then
            install -o root -g root -m 0600 "$backup" "$target"
        else
            rm -f -- "$target"
        fi
    done
}

if "$DEPLOY_SCRIPT" --config "$CONFIG_PATH"; then
    echo "AIOps 目标服务器环境配置已自动部署完成。"
    exit 0
fi

if [[ -f "$backup_dir/deploy.conf" ]]; then
    echo "新配置部署失败，正在恢复上一版配置。" >&2
    restore_previous_files
    if ! "$DEPLOY_SCRIPT" --config "$CONFIG_PATH"; then
        fail "新配置失败，上一版文件已恢复，但运行状态回滚也失败，请人工检查"
    fi
    fail "新配置部署失败，已恢复并重新应用上一版配置"
fi

fail "新配置部署失败，且目标服务器没有可恢复的上一版配置"
