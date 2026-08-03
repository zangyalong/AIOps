#!/bin/bash

set -euo pipefail

fail() {
    echo "alloy/install.sh: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：
  # 首次基础安装：不需要服务映射，也不会启动 Alloy
  sudo ./install.sh \
    --server web01 \
    --loki-url http://监控服务器:3100/loki/api/v1/push \
    --deploy-user aioms-deploy \
    --install-only \
    [--allow-docker-socket]

  # 管理员已有服务映射时直接安装配置
  sudo ./install.sh \
    --server web01 \
    --loki-url http://监控服务器:3100/loki/api/v1/push \
    --deploy-user aioms-deploy \
    --map ./services.map \
    [--allow-docker-socket] \
    [--no-start]

--install-only          只安装 Alloy 和固定配置应用器，不要求 services.map，不启动服务。
--allow-docker-socket   允许后续已审批配置采集 Docker 日志；该权限接近 Docker 控制权限。
--no-start              安装并校验已有映射，但不启动或重启 Alloy。
EOF
}

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly CONFIG_DIR="/etc/alloy"
readonly CONFIG_PATH="$CONFIG_DIR/config.alloy"
readonly AIOMS_CONFIG_DIR="/etc/aioms"
readonly TARGET_CONFIG="$AIOMS_CONFIG_DIR/alloy-target.conf"
readonly LIBEXEC_DIR="/usr/local/libexec/aioms"
readonly INSTALLED_GENERATOR="$LIBEXEC_DIR/generate-alloy-config"
readonly INSTALLED_APPLIER="$LIBEXEC_DIR/apply-alloy-config"
readonly DEPLOY_STATE_DIR="/var/lib/aioms-deploy"
readonly DEPLOY_INCOMING_DIR="$DEPLOY_STATE_DIR/incoming"

SERVER_NAME=""
LOKI_PUSH_URL=""
DEPLOY_USER="aioms-deploy"
SERVICE_MAP=""
ALLOW_DOCKER_SOCKET=false
INSTALL_ONLY=false
START_SERVICE=true

while (($# > 0)); do
    case "$1" in
        --server)
            (($# >= 2)) || fail "--server 缺少参数"
            SERVER_NAME="$2"
            shift 2
            ;;
        --loki-url)
            (($# >= 2)) || fail "--loki-url 缺少参数"
            LOKI_PUSH_URL="$2"
            shift 2
            ;;
        --deploy-user)
            (($# >= 2)) || fail "--deploy-user 缺少参数"
            DEPLOY_USER="$2"
            shift 2
            ;;
        --map)
            (($# >= 2)) || fail "--map 缺少参数"
            SERVICE_MAP="$2"
            shift 2
            ;;
        --allow-docker-socket)
            ALLOW_DOCKER_SOCKET=true
            shift
            ;;
        --install-only)
            INSTALL_ONLY=true
            START_SERVICE=false
            shift
            ;;
        --no-start)
            START_SERVICE=false
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "未知参数：$1"
            ;;
    esac
done

readonly USER_PATTERN='^[a-z_][a-z0-9_-]*[$]?$'
[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ "$SERVER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$ ]] || fail "必须提供合法 --server"
[[ "$LOKI_PUSH_URL" =~ ^https?://[^[:space:]]+/loki/api/v1/push$ ]] ||
    fail "--loki-url 必须是 http(s)://.../loki/api/v1/push"
[[ "$DEPLOY_USER" =~ $USER_PATTERN ]] || fail "--deploy-user 格式不合法"
id "$DEPLOY_USER" >/dev/null 2>&1 || fail "部署用户不存在：$DEPLOY_USER"

if [[ "$INSTALL_ONLY" == true ]]; then
    [[ -z "$SERVICE_MAP" ]] || fail "--install-only 不能同时指定 --map"
else
    [[ -n "$SERVICE_MAP" ]] || fail "必须提供 --map，或改用 --install-only"
    [[ -f "$SERVICE_MAP" ]] || fail "找不到映射文件：$SERVICE_MAP"
fi

map_uses_source() {
    local source_type="$1"
    awk -F'|' -v source_type="$source_type" '
        $0 !~ /^[[:space:]]*#/ && NF >= 5 && $5 == source_type { found = 1 }
        END { exit(found ? 0 : 1) }
    ' "$SERVICE_MAP"
}

if [[ "$INSTALL_ONLY" != true ]] && map_uses_source docker &&
    [[ "$ALLOW_DOCKER_SOCKET" != true ]]; then
    fail "映射包含 Docker 日志；请阅读 README 后显式增加 --allow-docker-socket"
fi

install_alloy_package() {
    if command -v alloy >/dev/null 2>&1; then
        echo "已检测到 Alloy：$(alloy --version | head -n 1)"
        return
    fi

    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        apt-get install -y ca-certificates gpg wget
        install -d -o root -g root -m 0755 /etc/apt/keyrings
        wget -O /etc/apt/keyrings/grafana.asc https://apt.grafana.com/gpg-full.key
        chmod 0644 /etc/apt/keyrings/grafana.asc
        printf '%s\n' \
            'deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main' \
            >/etc/apt/sources.list.d/grafana.list
        apt-get update
        apt-get install -y alloy
        return
    fi

    if command -v dnf >/dev/null 2>&1; then
        cat >/etc/yum.repos.d/grafana.repo <<'EOF'
[grafana]
name=grafana
baseurl=https://rpm.grafana.com
repo_gpgcheck=1
enabled=1
gpgcheck=1
gpgkey=https://rpm.grafana.com/gpg.key
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
EOF
        dnf install -y alloy
        return
    fi

    fail "只自动支持 Debian/Ubuntu（apt）和 RHEL/Fedora（dnf），请先手动安装 Alloy"
}

install_alloy_package
id alloy >/dev/null 2>&1 || fail "Alloy 包没有创建 alloy 用户"

for journal_group in adm systemd-journal; do
    if getent group "$journal_group" >/dev/null 2>&1; then
        usermod -aG "$journal_group" alloy
    fi
done

install -d -o root -g root -m 0755 "$LIBEXEC_DIR" "$AIOMS_CONFIG_DIR" "$DEPLOY_STATE_DIR"
install -d -o "$DEPLOY_USER" -g "$(id -gn "$DEPLOY_USER")" -m 0700 "$DEPLOY_INCOMING_DIR"
install -o root -g root -m 0755 "$SCRIPT_DIR/generate-config.sh" "$INSTALLED_GENERATOR"
install -o root -g root -m 0755 "$SCRIPT_DIR/aioms-alloy-apply" "$INSTALLED_APPLIER"

temp_target_config="$(mktemp "$AIOMS_CONFIG_DIR/.alloy-target.XXXXXX")"
cleanup_target_config() {
    rm -f -- "$temp_target_config"
}
trap cleanup_target_config EXIT
cat >"$temp_target_config" <<EOF
SERVER_NAME=$SERVER_NAME
LOKI_PUSH_URL=$LOKI_PUSH_URL
ALLOW_DOCKER_SOCKET=$ALLOW_DOCKER_SOCKET
DEPLOY_USER=$DEPLOY_USER
EOF
install -o root -g root -m 0600 "$temp_target_config" "$TARGET_CONFIG"
rm -f -- "$temp_target_config"
trap - EXIT

install -d -o alloy -g alloy -m 0750 /var/lib/alloy

if [[ "$INSTALL_ONLY" == true ]]; then
    if [[ ! -f "$CONFIG_PATH" ]]; then
        systemctl disable --now alloy >/dev/null 2>&1 || true
    fi
    echo "Alloy 基础组件已安装，但没有服务映射，因此未启动。"
    echo "服务注册后由 AIOMS 预览并审批应用日志配置。"
    exit 0
fi

if map_uses_source docker; then
    [[ -S /var/run/docker.sock ]] || fail "映射需要 Docker，但 /var/run/docker.sock 不存在"
    docker_group="$(stat -c '%G' /var/run/docker.sock)"
    [[ -n "$docker_group" && "$docker_group" != "UNKNOWN" ]] ||
        fail "无法确定 Docker socket 所属组"
    getent group "$docker_group" >/dev/null 2>&1 ||
        fail "Docker socket 所属组 $docker_group 不存在"
    usermod -aG "$docker_group" alloy
    echo "已把 alloy 加入 $docker_group 组；该组通常拥有接近 root 的 Docker 控制权限。"
fi

generated_config="$(mktemp /tmp/aioms-alloy-config.XXXXXX)"
cleanup_generated_config() {
    rm -f -- "$generated_config"
}
trap cleanup_generated_config EXIT

"$INSTALLED_GENERATOR" "$SERVICE_MAP" "$SERVER_NAME" "$LOKI_PUSH_URL" "$generated_config"
alloy validate "$generated_config"

install -d -o root -g alloy -m 0750 "$CONFIG_DIR"
backup_path=""
if [[ -f "$CONFIG_PATH" ]]; then
    backup_path="$CONFIG_PATH.backup.$(date +%Y%m%d%H%M%S)"
    cp -a -- "$CONFIG_PATH" "$backup_path"
    echo "原配置已备份：$backup_path"
fi
install -o root -g alloy -m 0640 "$generated_config" "$CONFIG_PATH"

if map_uses_source file; then
    echo "注意：映射包含文件日志。请按 README 给 alloy 用户配置日志目录 ACL。"
fi

if [[ "$START_SERVICE" != true ]]; then
    echo "Alloy 配置已安装但未启动。服务注册后也可通过审批 Tool 应用并启动。"
    exit 0
fi

systemctl enable alloy
if ! systemctl restart alloy; then
    if [[ -n "$backup_path" ]]; then
        cp -a -- "$backup_path" "$CONFIG_PATH"
        systemctl restart alloy || true
        fail "Alloy 启动失败，已恢复并尝试重启旧配置：$backup_path"
    fi
    fail "Alloy 首次启动失败，请查看：journalctl -u alloy -n 100 --no-pager"
fi

systemctl is-active --quiet alloy || fail "Alloy 未处于 active 状态"
echo "Alloy 已安装并启动。下一步运行：$SCRIPT_DIR/check.sh"
