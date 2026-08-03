#!/bin/bash

set -euo pipefail

fail() {
    echo "alloy/install.sh: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：
  sudo ./install.sh \
    --server web01 \
    --loki-url http://监控服务器:3100/loki/api/v1/push \
    --map ./services.map \
    [--allow-docker-socket] \
    [--no-start]

--allow-docker-socket  仅当 services.map 使用 docker 日志源时需要。
                       Docker socket 权限很高，必须显式确认。
--no-start             只安装和校验配置，不启动/重启 Alloy。
EOF
}

SERVER_NAME=""
LOKI_PUSH_URL=""
SERVICE_MAP=""
ALLOW_DOCKER_SOCKET=false
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
        --map)
            (($# >= 2)) || fail "--map 缺少参数"
            SERVICE_MAP="$2"
            shift 2
            ;;
        --allow-docker-socket)
            ALLOW_DOCKER_SOCKET=true
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

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ -n "$SERVER_NAME" ]] || fail "必须提供 --server"
[[ -n "$LOKI_PUSH_URL" ]] || fail "必须提供 --loki-url"
[[ -n "$SERVICE_MAP" ]] || fail "必须提供 --map"
[[ -f "$SERVICE_MAP" ]] || fail "找不到映射文件：$SERVICE_MAP"

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly CONFIG_DIR="/etc/alloy"
readonly CONFIG_PATH="$CONFIG_DIR/config.alloy"
readonly GENERATED_CONFIG="$(mktemp /tmp/aioms-alloy-config.XXXXXX)"

cleanup() {
    rm -f -- "$GENERATED_CONFIG"
}
trap cleanup EXIT

map_uses_source() {
    local source_type="$1"
    awk -F'|' -v source_type="$source_type" '
        $0 !~ /^[[:space:]]*#/ && NF >= 5 && $5 == source_type { found = 1 }
        END { exit(found ? 0 : 1) }
    ' "$SERVICE_MAP"
}

if map_uses_source docker && [[ "$ALLOW_DOCKER_SOCKET" != true ]]; then
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

if map_uses_source docker; then
    [[ -S /var/run/docker.sock ]] || fail "映射需要 Docker，但 /var/run/docker.sock 不存在"
    docker_group="$(stat -c '%G' /var/run/docker.sock)"
    [[ -n "$docker_group" && "$docker_group" != "UNKNOWN" ]] ||
        fail "无法确定 Docker socket 所属组"
    getent group "$docker_group" >/dev/null 2>&1 ||
        fail "Docker socket 所属组 $docker_group 不存在于组数据库"
    usermod -aG "$docker_group" alloy
    echo "已把 alloy 加入 $docker_group 组；该组通常拥有接近 root 的 Docker 控制权限。"
fi

"$SCRIPT_DIR/generate-config.sh" \
    "$SERVICE_MAP" "$SERVER_NAME" "$LOKI_PUSH_URL" "$GENERATED_CONFIG"
alloy validate "$GENERATED_CONFIG"

install -d -o root -g alloy -m 0750 "$CONFIG_DIR"
backup_path=""
if [[ -f "$CONFIG_PATH" ]]; then
    backup_path="$CONFIG_PATH.backup.$(date +%Y%m%d%H%M%S)"
    cp -a -- "$CONFIG_PATH" "$backup_path"
    echo "原配置已备份：$backup_path"
fi
install -o root -g alloy -m 0640 "$GENERATED_CONFIG" "$CONFIG_PATH"

install -d -o alloy -g alloy -m 0750 /var/lib/alloy

if map_uses_source file; then
    echo "注意：映射包含文件日志。请按 README 给 alloy 用户配置日志目录 ACL。"
fi

if [[ "$START_SERVICE" != true ]]; then
    echo "Alloy 配置已安装但未启动。验证后执行：systemctl enable --now alloy"
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
