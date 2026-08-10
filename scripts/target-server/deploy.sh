#!/bin/bash

set -euo pipefail

fail() {
    echo "deploy.sh: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：
  sudo ./deploy.sh
  sudo ./deploy.sh --config /path/to/deploy.conf

默认读取脚本所在目录的 deploy.conf。先执行：
  cp deploy.conf.example deploy.conf

首次安装只修改 deploy.conf，不需要预先填写服务清单。
EOF
}

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="$SCRIPT_DIR/deploy.conf"

while (($# > 0)); do
    case "$1" in
        --config)
            (($# >= 2)) || fail "--config 缺少路径"
            CONFIG_PATH="$2"
            shift 2
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
[[ -f "$CONFIG_PATH" ]] || fail "找不到配置文件：$CONFIG_PATH（请先复制 deploy.conf.example）"

AIOMS_SSH_USER=""
SERVICE_CONTROL_ALLOWLIST=""
ENABLE_ALLOY="true"
SERVER_NAME=""
LOKI_PUSH_URL=""
ALLOY_APT_REPO_URL="https://apt.grafana.com"
ALLOY_APT_GPG_KEY_URL="https://apt.grafana.com/gpg-full.key"
ALLOY_RPM_REPO_URL="https://mirrors.tuna.tsinghua.edu.cn/grafana/yum/rpm"
ALLOY_RPM_GPG_KEY_URL="https://rpm.grafana.com/gpg.key"
ALLOY_PACKAGE_PROXY_URL=""
ALLOY_SERVICE_MAP=""
ALLOW_DOCKER_SOCKET="false"
START_ALLOY="false"
ENVIRONMENT="prod"
ENABLE_NODE_EXPORTER="false"
NODE_EXPORTER_IMAGE=""
ENABLE_CADVISOR="false"
CADVISOR_IMAGE=""
DOCKER_ROOT_DIR="/var/lib/docker"
ENABLE_MYSQL_EXPORTER="false"
MYSQL_EXPORTER_IMAGE=""
MYSQL_EXPORTER_CONFIG=""
MYSQL_EXPORTER_NETWORK="host"
ENABLE_POSTGRES_EXPORTER="false"
POSTGRES_EXPORTER_IMAGE=""
POSTGRES_EXPORTER_URI=""
POSTGRES_EXPORTER_USER=""
POSTGRES_EXPORTER_PASSWORD_FILE=""
POSTGRES_EXPORTER_NETWORK="host"
ENABLE_REDIS_EXPORTER="false"
REDIS_EXPORTER_IMAGE=""
REDIS_EXPORTER_ADDRESS=""
REDIS_EXPORTER_USER=""
REDIS_EXPORTER_PASSWORD_FILE=""
REDIS_EXPORTER_NETWORK="host"
START_EXPORTERS="true"
EXPORTER_IMAGE_MIRROR_PREFIX="m.daocloud.io"
SEEN_KEYS="|"

trim() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

set_config_value() {
    local key="$1"
    local value="$2"

    case "$key" in
        AIOMS_SSH_USER) AIOMS_SSH_USER="$value" ;;
        SERVICE_CONTROL_ALLOWLIST) SERVICE_CONTROL_ALLOWLIST="$value" ;;
        ENABLE_ALLOY) ENABLE_ALLOY="$value" ;;
        SERVER_NAME) SERVER_NAME="$value" ;;
        LOKI_PUSH_URL) LOKI_PUSH_URL="$value" ;;
        ALLOY_APT_REPO_URL) ALLOY_APT_REPO_URL="$value" ;;
        ALLOY_APT_GPG_KEY_URL) ALLOY_APT_GPG_KEY_URL="$value" ;;
        ALLOY_RPM_REPO_URL) ALLOY_RPM_REPO_URL="$value" ;;
        ALLOY_RPM_GPG_KEY_URL) ALLOY_RPM_GPG_KEY_URL="$value" ;;
        ALLOY_PACKAGE_PROXY_URL) ALLOY_PACKAGE_PROXY_URL="$value" ;;
        ALLOY_SERVICE_MAP) ALLOY_SERVICE_MAP="$value" ;;
        ALLOW_DOCKER_SOCKET) ALLOW_DOCKER_SOCKET="$value" ;;
        START_ALLOY) START_ALLOY="$value" ;;
        ENVIRONMENT) ENVIRONMENT="$value" ;;
        ENABLE_NODE_EXPORTER) ENABLE_NODE_EXPORTER="$value" ;;
        NODE_EXPORTER_IMAGE) NODE_EXPORTER_IMAGE="$value" ;;
        ENABLE_CADVISOR) ENABLE_CADVISOR="$value" ;;
        CADVISOR_IMAGE) CADVISOR_IMAGE="$value" ;;
        DOCKER_ROOT_DIR) DOCKER_ROOT_DIR="$value" ;;
        ENABLE_MYSQL_EXPORTER) ENABLE_MYSQL_EXPORTER="$value" ;;
        MYSQL_EXPORTER_IMAGE) MYSQL_EXPORTER_IMAGE="$value" ;;
        MYSQL_EXPORTER_CONFIG) MYSQL_EXPORTER_CONFIG="$value" ;;
        MYSQL_EXPORTER_NETWORK) MYSQL_EXPORTER_NETWORK="$value" ;;
        ENABLE_POSTGRES_EXPORTER) ENABLE_POSTGRES_EXPORTER="$value" ;;
        POSTGRES_EXPORTER_IMAGE) POSTGRES_EXPORTER_IMAGE="$value" ;;
        POSTGRES_EXPORTER_URI) POSTGRES_EXPORTER_URI="$value" ;;
        POSTGRES_EXPORTER_USER) POSTGRES_EXPORTER_USER="$value" ;;
        POSTGRES_EXPORTER_PASSWORD_FILE) POSTGRES_EXPORTER_PASSWORD_FILE="$value" ;;
        POSTGRES_EXPORTER_NETWORK) POSTGRES_EXPORTER_NETWORK="$value" ;;
        ENABLE_REDIS_EXPORTER) ENABLE_REDIS_EXPORTER="$value" ;;
        REDIS_EXPORTER_IMAGE) REDIS_EXPORTER_IMAGE="$value" ;;
        REDIS_EXPORTER_ADDRESS) REDIS_EXPORTER_ADDRESS="$value" ;;
        REDIS_EXPORTER_USER) REDIS_EXPORTER_USER="$value" ;;
        REDIS_EXPORTER_PASSWORD_FILE) REDIS_EXPORTER_PASSWORD_FILE="$value" ;;
        REDIS_EXPORTER_NETWORK) REDIS_EXPORTER_NETWORK="$value" ;;
        START_EXPORTERS) START_EXPORTERS="$value" ;;
        EXPORTER_IMAGE_MIRROR_PREFIX) EXPORTER_IMAGE_MIRROR_PREFIX="$value" ;;
        *) fail "配置项不受支持：$key" ;;
    esac
}

line_number=0
while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    ((line_number += 1))
    line="${raw_line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *=* ]] || fail "配置文件第 $line_number 行不是 KEY=VALUE 格式"

    key="$(trim "${line%%=*}")"
    value="$(trim "${line#*=}")"
    [[ -n "$key" ]] || fail "配置文件第 $line_number 行缺少配置项名称"
    [[ "$SEEN_KEYS" != *"|$key|"* ]] || fail "配置项重复：$key"

    set_config_value "$key" "$value"
    SEEN_KEYS+="$key|"
done <"$CONFIG_PATH"

# 仓库字段留空时使用各平台默认地址，代理留空表示直连。
ALLOY_APT_REPO_URL="${ALLOY_APT_REPO_URL:-https://apt.grafana.com}"
ALLOY_APT_GPG_KEY_URL="${ALLOY_APT_GPG_KEY_URL:-https://apt.grafana.com/gpg-full.key}"
ALLOY_RPM_REPO_URL="${ALLOY_RPM_REPO_URL:-https://mirrors.tuna.tsinghua.edu.cn/grafana/yum/rpm}"
ALLOY_RPM_GPG_KEY_URL="${ALLOY_RPM_GPG_KEY_URL:-https://rpm.grafana.com/gpg.key}"

validate_boolean() {
    local key="$1"
    local value="$2"
    [[ "$value" == "true" || "$value" == "false" ]] ||
        fail "$key 只能填写 true 或 false"
}

validate_http_url() {
    local key="$1"
    local value="$2"

    [[ "$value" =~ ^https?://[^[:space:]]+$ ]] ||
        fail "$key 必须是没有空格的 http(s) URL"
}

[[ -n "$AIOMS_SSH_USER" ]] || fail "AIOMS_SSH_USER 不能为空"
[[ "$AIOMS_SSH_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] ||
    fail "AIOMS_SSH_USER 格式不合法"
[[ "$AIOMS_SSH_USER" != "root" ]] || fail "AIOMS_SSH_USER 不能是 root"
validate_boolean "ENABLE_ALLOY" "$ENABLE_ALLOY"
validate_boolean "ALLOW_DOCKER_SOCKET" "$ALLOW_DOCKER_SOCKET"
validate_boolean "START_ALLOY" "$START_ALLOY"
validate_boolean "ENABLE_NODE_EXPORTER" "$ENABLE_NODE_EXPORTER"
validate_boolean "ENABLE_CADVISOR" "$ENABLE_CADVISOR"
validate_boolean "ENABLE_MYSQL_EXPORTER" "$ENABLE_MYSQL_EXPORTER"
validate_boolean "ENABLE_POSTGRES_EXPORTER" "$ENABLE_POSTGRES_EXPORTER"
validate_boolean "ENABLE_REDIS_EXPORTER" "$ENABLE_REDIS_EXPORTER"
validate_boolean "START_EXPORTERS" "$START_EXPORTERS"

[[ -x "$SCRIPT_DIR/discovery/install.sh" ]] ||
    fail "缺少可执行的只读网关安装器：$SCRIPT_DIR/discovery/install.sh"
[[ -x "$SCRIPT_DIR/root-read/install.sh" ]] ||
    fail "缺少可执行的 root 只读沙箱安装器：$SCRIPT_DIR/root-read/install.sh"
[[ -x "$SCRIPT_DIR/actions/install.sh" ]] ||
    fail "缺少可执行的服务控制写网关安装器：$SCRIPT_DIR/actions/install.sh"
[[ -x "$SCRIPT_DIR/files/install.sh" ]] ||
    fail "缺少可执行的文件访问网关安装器：$SCRIPT_DIR/files/install.sh"

alloy_args=()
if [[ "$ENABLE_ALLOY" == "true" ]]; then
    [[ -n "$SERVER_NAME" ]] || fail "ENABLE_ALLOY=true 时 SERVER_NAME 不能为空"
    [[ -n "$LOKI_PUSH_URL" ]] || fail "ENABLE_ALLOY=true 时 LOKI_PUSH_URL 不能为空"
    validate_http_url "ALLOY_APT_REPO_URL" "$ALLOY_APT_REPO_URL"
    validate_http_url "ALLOY_APT_GPG_KEY_URL" "$ALLOY_APT_GPG_KEY_URL"
    validate_http_url "ALLOY_RPM_REPO_URL" "$ALLOY_RPM_REPO_URL"
    validate_http_url "ALLOY_RPM_GPG_KEY_URL" "$ALLOY_RPM_GPG_KEY_URL"
    if [[ -n "$ALLOY_PACKAGE_PROXY_URL" ]]; then
        validate_http_url "ALLOY_PACKAGE_PROXY_URL" "$ALLOY_PACKAGE_PROXY_URL"
    fi
    [[ -x "$SCRIPT_DIR/alloy/install.sh" ]] ||
        fail "缺少可执行的 Alloy 安装器：$SCRIPT_DIR/alloy/install.sh"
    [[ -x "$SCRIPT_DIR/alloy/aioms-alloy-apply" ]] ||
        fail "缺少固定 Alloy 配置应用器：$SCRIPT_DIR/alloy/aioms-alloy-apply"

    alloy_args=(
        --server "$SERVER_NAME"
        --loki-url "$LOKI_PUSH_URL"
        --ssh-user "$AIOMS_SSH_USER"
        --apt-repo-url "$ALLOY_APT_REPO_URL"
        --apt-gpg-key-url "$ALLOY_APT_GPG_KEY_URL"
        --rpm-repo-url "$ALLOY_RPM_REPO_URL"
        --rpm-gpg-key-url "$ALLOY_RPM_GPG_KEY_URL"
    )

    if [[ -n "$ALLOY_PACKAGE_PROXY_URL" ]]; then
        alloy_args+=(--package-proxy-url "$ALLOY_PACKAGE_PROXY_URL")
    fi

    if [[ -n "$ALLOY_SERVICE_MAP" ]]; then
        if [[ "$ALLOY_SERVICE_MAP" == /* ]]; then
            service_map_path="$ALLOY_SERVICE_MAP"
        else
            service_map_path="$SCRIPT_DIR/$ALLOY_SERVICE_MAP"
        fi
        [[ -f "$service_map_path" ]] || fail "找不到 Alloy 服务映射：$service_map_path"
        alloy_args+=(--map "$service_map_path")
    else
        [[ "$START_ALLOY" == "false" ]] ||
            fail "没有 ALLOY_SERVICE_MAP 时 START_ALLOY 必须为 false"
        alloy_args+=(--install-only)
    fi

    if [[ "$ALLOW_DOCKER_SOCKET" == "true" ]]; then
        alloy_args+=(--allow-docker-socket)
    fi
    if [[ -n "$ALLOY_SERVICE_MAP" && "$START_ALLOY" != "true" ]]; then
        alloy_args+=(--no-start)
    fi
fi

resolve_package_path() {
    local configured_path="$1"
    if [[ "$configured_path" == /* ]]; then
        printf '%s' "$configured_path"
    else
        printf '%s/%s' "$SCRIPT_DIR" "$configured_path"
    fi
}

metrics_enabled=false
metrics_args=()
[[ -x "$SCRIPT_DIR/metrics/install.sh" ]] ||
    fail "缺少可执行的指标安装器：$SCRIPT_DIR/metrics/install.sh"
for enabled in \
    "$ENABLE_NODE_EXPORTER" \
    "$ENABLE_CADVISOR" \
    "$ENABLE_MYSQL_EXPORTER" \
    "$ENABLE_POSTGRES_EXPORTER" \
    "$ENABLE_REDIS_EXPORTER"; do
    if [[ "$enabled" == "true" ]]; then
        metrics_enabled=true
        break
    fi
done

if [[ "$metrics_enabled" == "true" ]]; then
    [[ -n "$SERVER_NAME" ]] || fail "启用 exporter 时 SERVER_NAME 不能为空"
    [[ -n "$ENVIRONMENT" ]] || fail "启用 exporter 时 ENVIRONMENT 不能为空"
    metrics_args=(
        --server "$SERVER_NAME"
        --environment "$ENVIRONMENT"
        --image-mirror-prefix "$EXPORTER_IMAGE_MIRROR_PREFIX"
    )

    if [[ "$ENABLE_NODE_EXPORTER" == "true" ]]; then
        [[ -n "$NODE_EXPORTER_IMAGE" ]] || fail "node_exporter 镜像不能为空"
        metrics_args+=(--node-image "$NODE_EXPORTER_IMAGE")
    fi

    if [[ "$ENABLE_CADVISOR" == "true" ]]; then
        [[ -n "$CADVISOR_IMAGE" ]] || fail "cAdvisor 镜像不能为空"
        [[ -n "$DOCKER_ROOT_DIR" ]] || fail "Docker 数据目录不能为空"
        metrics_args+=(
            --cadvisor-image "$CADVISOR_IMAGE"
            --docker-root-dir "$DOCKER_ROOT_DIR"
        )
    fi

    if [[ "$ENABLE_MYSQL_EXPORTER" == "true" ]]; then
        [[ -n "$MYSQL_EXPORTER_IMAGE" ]] || fail "MySQL exporter 镜像不能为空"
        [[ -n "$MYSQL_EXPORTER_CONFIG" ]] || fail "MySQL exporter 配置路径不能为空"
        mysql_config_path="$(resolve_package_path "$MYSQL_EXPORTER_CONFIG")"
        [[ -f "$mysql_config_path" ]] || fail "找不到 MySQL exporter 配置：$mysql_config_path"
        metrics_args+=(
            --mysql-image "$MYSQL_EXPORTER_IMAGE"
            --mysql-config "$mysql_config_path"
            --mysql-network "$MYSQL_EXPORTER_NETWORK"
        )
    fi

    if [[ "$ENABLE_POSTGRES_EXPORTER" == "true" ]]; then
        [[ -n "$POSTGRES_EXPORTER_IMAGE" ]] || fail "PostgreSQL exporter 镜像不能为空"
        [[ -n "$POSTGRES_EXPORTER_URI" ]] || fail "PostgreSQL exporter URI 不能为空"
        [[ -n "$POSTGRES_EXPORTER_USER" ]] || fail "PostgreSQL exporter 用户不能为空"
        [[ -n "$POSTGRES_EXPORTER_PASSWORD_FILE" ]] ||
            fail "PostgreSQL exporter 密码文件路径不能为空"
        postgres_password_path="$(resolve_package_path "$POSTGRES_EXPORTER_PASSWORD_FILE")"
        [[ -f "$postgres_password_path" ]] ||
            fail "找不到 PostgreSQL exporter 密码文件：$postgres_password_path"
        metrics_args+=(
            --postgres-image "$POSTGRES_EXPORTER_IMAGE"
            --postgres-uri "$POSTGRES_EXPORTER_URI"
            --postgres-user "$POSTGRES_EXPORTER_USER"
            --postgres-password-file "$postgres_password_path"
            --postgres-network "$POSTGRES_EXPORTER_NETWORK"
        )
    fi

    if [[ "$ENABLE_REDIS_EXPORTER" == "true" ]]; then
        [[ -n "$REDIS_EXPORTER_IMAGE" ]] || fail "Redis exporter 镜像不能为空"
        [[ -n "$REDIS_EXPORTER_ADDRESS" ]] || fail "Redis exporter 地址不能为空"
        metrics_args+=(
            --redis-image "$REDIS_EXPORTER_IMAGE"
            --redis-address "$REDIS_EXPORTER_ADDRESS"
            --redis-user "$REDIS_EXPORTER_USER"
            --redis-network "$REDIS_EXPORTER_NETWORK"
        )
        if [[ -n "$REDIS_EXPORTER_PASSWORD_FILE" ]]; then
            redis_password_path="$(resolve_package_path "$REDIS_EXPORTER_PASSWORD_FILE")"
            [[ -f "$redis_password_path" ]] ||
                fail "找不到 Redis exporter 密码文件：$redis_password_path"
            metrics_args+=(--redis-password-file "$redis_password_path")
        fi
    fi

    if [[ "$START_EXPORTERS" != "true" ]]; then
        metrics_args+=(--no-start)
    fi
fi

echo "[1/6] 安装 AIOMS root 只读诊断沙箱"
"$SCRIPT_DIR/root-read/install.sh" "$AIOMS_SSH_USER"

echo "[2/6] 安装 AIOMS 只读服务发现网关"
"$SCRIPT_DIR/discovery/install.sh" "$AIOMS_SSH_USER"

echo "[3/6] 安装 AIOMS 服务控制写网关"
"$SCRIPT_DIR/actions/install.sh" \
    --ssh-user "$AIOMS_SSH_USER" \
    --allowlist "$SERVICE_CONTROL_ALLOWLIST"

echo "[4/6] 安装 AIOMS 受控文件访问网关"
"$SCRIPT_DIR/files/install.sh" "$AIOMS_SSH_USER"

if [[ "$ENABLE_ALLOY" == "true" ]]; then
    if [[ -n "$ALLOY_SERVICE_MAP" ]]; then
        echo "[5/6] 安装并配置 Alloy 日志采集"
    else
        echo "[5/6] 安装 Alloy 基础组件，等待服务发现后生成配置"
    fi
    "$SCRIPT_DIR/alloy/install.sh" "${alloy_args[@]}"
else
    echo "[5/6] 已按配置跳过 Alloy"
fi

if [[ "$metrics_enabled" == "true" ]]; then
    echo "[6/6] 安装并配置 Prometheus 指标 exporter"
    "$SCRIPT_DIR/metrics/install.sh" "${metrics_args[@]}"
else
    echo "[6/6] 全部指标 exporter 已关闭，清理本配置包曾部署的指标容器"
    "$SCRIPT_DIR/metrics/install.sh" --disable-all
fi

echo "目标服务器配置完成。"
echo "服务器：$SERVER_NAME"
echo "SSH 用户：$AIOMS_SSH_USER"
if [[ "$ENABLE_ALLOY" == "true" ]]; then
    echo "Alloy 检查：sudo $SCRIPT_DIR/alloy/check.sh"
fi
if [[ "$metrics_enabled" == "true" ]]; then
    echo "Exporter 检查：sudo $SCRIPT_DIR/metrics/check.sh"
fi
