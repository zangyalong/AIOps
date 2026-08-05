#!/bin/bash

set -euo pipefail

fail() {
    echo "metrics/install.sh: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：
  sudo ./install.sh --server web01 --environment prod \
    [--image-mirror-prefix REGISTRY] \
    [--node-image IMAGE] \
    [--cadvisor-image IMAGE --docker-root-dir /var/lib/docker] \
    [--mysql-image IMAGE --mysql-config FILE --mysql-network NETWORK] \
    [--postgres-image IMAGE --postgres-uri URI --postgres-user USER \
      --postgres-password-file FILE --postgres-network NETWORK] \
    [--redis-image IMAGE --redis-address URI [--redis-user USER] \
      [--redis-password-file FILE] --redis-network NETWORK] \
    [--disable-all] \
    [--no-start]

只传入需要启用的组件。安装器生成 /etc/aioms-exporters/compose.yaml，
并通过 Docker Compose 管理 aioms-exporters 项目。

--image-mirror-prefix REGISTRY
                       仅本次拉取使用的镜像代理前缀；默认 m.daocloud.io，传空值则直连原仓库。
EOF
}

SERVER_NAME=""
ENVIRONMENT=""
NODE_IMAGE=""
CADVISOR_IMAGE=""
DOCKER_ROOT_DIR=""
MYSQL_IMAGE=""
MYSQL_CONFIG=""
MYSQL_NETWORK="host"
POSTGRES_IMAGE=""
POSTGRES_URI=""
POSTGRES_USER=""
POSTGRES_PASSWORD_FILE=""
POSTGRES_NETWORK="host"
REDIS_IMAGE=""
REDIS_ADDRESS=""
REDIS_USER=""
REDIS_PASSWORD_FILE=""
REDIS_NETWORK="host"
START_EXPORTERS=true
DISABLE_ALL=false
IMAGE_MIRROR_PREFIX="m.daocloud.io"

while (($# > 0)); do
    case "$1" in
        --server)
            (($# >= 2)) || fail "--server 缺少参数"
            SERVER_NAME="$2"
            shift 2
            ;;
        --environment)
            (($# >= 2)) || fail "--environment 缺少参数"
            ENVIRONMENT="$2"
            shift 2
            ;;
        --image-mirror-prefix)
            (($# >= 2)) || fail "--image-mirror-prefix 缺少参数"
            IMAGE_MIRROR_PREFIX="$2"
            shift 2
            ;;
        --node-image)
            (($# >= 2)) || fail "--node-image 缺少参数"
            NODE_IMAGE="$2"
            shift 2
            ;;
        --cadvisor-image)
            (($# >= 2)) || fail "--cadvisor-image 缺少参数"
            CADVISOR_IMAGE="$2"
            shift 2
            ;;
        --docker-root-dir)
            (($# >= 2)) || fail "--docker-root-dir 缺少参数"
            DOCKER_ROOT_DIR="$2"
            shift 2
            ;;
        --mysql-image)
            (($# >= 2)) || fail "--mysql-image 缺少参数"
            MYSQL_IMAGE="$2"
            shift 2
            ;;
        --mysql-config)
            (($# >= 2)) || fail "--mysql-config 缺少参数"
            MYSQL_CONFIG="$2"
            shift 2
            ;;
        --mysql-network)
            (($# >= 2)) || fail "--mysql-network 缺少参数"
            MYSQL_NETWORK="$2"
            shift 2
            ;;
        --postgres-image)
            (($# >= 2)) || fail "--postgres-image 缺少参数"
            POSTGRES_IMAGE="$2"
            shift 2
            ;;
        --postgres-uri)
            (($# >= 2)) || fail "--postgres-uri 缺少参数"
            POSTGRES_URI="$2"
            shift 2
            ;;
        --postgres-user)
            (($# >= 2)) || fail "--postgres-user 缺少参数"
            POSTGRES_USER="$2"
            shift 2
            ;;
        --postgres-password-file)
            (($# >= 2)) || fail "--postgres-password-file 缺少参数"
            POSTGRES_PASSWORD_FILE="$2"
            shift 2
            ;;
        --postgres-network)
            (($# >= 2)) || fail "--postgres-network 缺少参数"
            POSTGRES_NETWORK="$2"
            shift 2
            ;;
        --redis-image)
            (($# >= 2)) || fail "--redis-image 缺少参数"
            REDIS_IMAGE="$2"
            shift 2
            ;;
        --redis-address)
            (($# >= 2)) || fail "--redis-address 缺少参数"
            REDIS_ADDRESS="$2"
            shift 2
            ;;
        --redis-user)
            (($# >= 2)) || fail "--redis-user 缺少参数"
            REDIS_USER="$2"
            shift 2
            ;;
        --redis-password-file)
            (($# >= 2)) || fail "--redis-password-file 缺少参数"
            REDIS_PASSWORD_FILE="$2"
            shift 2
            ;;
        --redis-network)
            (($# >= 2)) || fail "--redis-network 缺少参数"
            REDIS_NETWORK="$2"
            shift 2
            ;;
        --no-start)
            START_EXPORTERS=false
            shift
            ;;
        --disable-all)
            DISABLE_ALL=true
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

detect_compose() {
    command -v docker >/dev/null 2>&1 || fail "启用指标采集需要先安装 Docker"
    docker info >/dev/null 2>&1 || fail "Docker daemon 不可用"
    if docker compose version >/dev/null 2>&1; then
        COMPOSE=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE=(docker-compose)
    else
        fail "缺少 Docker Compose；请安装 docker compose 插件或 docker-compose"
    fi
}

if [[ "$DISABLE_ALL" == "true" ]]; then
    readonly EXISTING_CONFIG="/etc/aioms-exporters/compose.yaml"
    if [[ ! -f "$EXISTING_CONFIG" ]]; then
        echo "全部 exporter 已关闭，服务器上没有需要停止的 AIOMS 指标栈。"
        exit 0
    fi
    detect_compose
    "${COMPOSE[@]}" --project-name aioms-exporters -f "$EXISTING_CONFIG" down --remove-orphans
    echo "全部 AIOMS exporter 已停止；配置和凭据备份仍保留在 /etc/aioms-exporters。"
    exit 0
fi

[[ "$SERVER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$ ]] || fail "server 格式不合法"
[[ "$ENVIRONMENT" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$ ]] || fail "environment 格式不合法"
IMAGE_MIRROR_PREFIX="${IMAGE_MIRROR_PREFIX%/}"
if [[ -n "$IMAGE_MIRROR_PREFIX" ]]; then
    [[ "$IMAGE_MIRROR_PREFIX" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$ ]] ||
        fail "镜像代理前缀格式不合法：$IMAGE_MIRROR_PREFIX"
    [[ "$IMAGE_MIRROR_PREFIX" != *"://"* ]] ||
        fail "镜像代理前缀不要包含 http:// 或 https://"
fi

enabled_count=0
for image in \
    "$NODE_IMAGE" "$CADVISOR_IMAGE" "$MYSQL_IMAGE" "$POSTGRES_IMAGE" "$REDIS_IMAGE"; do
    [[ -z "$image" ]] && continue
    [[ "$image" =~ ^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,299}$ ]] ||
        fail "Docker 镜像格式不合法：$image"
    ((enabled_count += 1))
done
((enabled_count > 0)) || fail "至少需要启用一个 exporter"

if [[ -n "$CADVISOR_IMAGE" ]]; then
    [[ "$DOCKER_ROOT_DIR" == /* ]] || fail "Docker 数据目录必须是绝对路径"
    [[ "$DOCKER_ROOT_DIR" != *".."* ]] || fail "Docker 数据目录不能包含 .."
    [[ -d "$DOCKER_ROOT_DIR" ]] || fail "Docker 数据目录不存在：$DOCKER_ROOT_DIR"
    [[ -e /dev/kmsg ]] || fail "cAdvisor 需要 /dev/kmsg，但当前服务器不存在"
    [[ -d /dev/disk ]] || fail "cAdvisor 需要 /dev/disk，但当前服务器不存在"
fi

if [[ -n "$MYSQL_IMAGE" ]]; then
    [[ -f "$MYSQL_CONFIG" ]] || fail "找不到 MySQL exporter 配置：$MYSQL_CONFIG"
fi

if [[ -n "$POSTGRES_IMAGE" ]]; then
    [[ -n "$POSTGRES_URI" ]] || fail "PostgreSQL exporter 缺少连接 URI"
    [[ "$POSTGRES_URI" != *$'\n'* && ${#POSTGRES_URI} -le 500 ]] ||
        fail "PostgreSQL URI 格式不合法或过长"
    [[ "$POSTGRES_USER" =~ ^[A-Za-z0-9_.-]{1,100}$ ]] ||
        fail "PostgreSQL 监控用户名格式不合法"
    [[ -s "$POSTGRES_PASSWORD_FILE" ]] ||
        fail "找不到 PostgreSQL exporter 密码文件或文件为空：$POSTGRES_PASSWORD_FILE"
fi

if [[ -n "$REDIS_IMAGE" ]]; then
    [[ "$REDIS_ADDRESS" =~ ^rediss?://[^[:space:]]+$ ]] ||
        fail "Redis 地址必须是 redis:// 或 rediss:// URI"
    [[ -z "$REDIS_USER" || "$REDIS_USER" =~ ^[A-Za-z0-9_.-]{1,100}$ ]] ||
        fail "Redis 监控用户名格式不合法"
    if [[ -n "$REDIS_PASSWORD_FILE" ]]; then
        [[ -s "$REDIS_PASSWORD_FILE" ]] ||
            fail "找不到 Redis exporter 密码文件或文件为空：$REDIS_PASSWORD_FILE"
    fi
fi

detect_compose

validate_database_network() {
    local component="$1"
    local network="$2"
    [[ "$network" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] ||
        fail "$component Docker 网络名格式不合法：$network"
    if [[ "$network" != "host" ]]; then
        docker network inspect "$network" >/dev/null 2>&1 ||
            fail "$component 指定的 Docker 网络不存在：$network"
    fi
}

if [[ -n "$MYSQL_IMAGE" ]]; then
    validate_database_network "MySQL exporter" "$MYSQL_NETWORK"
fi
if [[ -n "$POSTGRES_IMAGE" ]]; then
    validate_database_network "PostgreSQL exporter" "$POSTGRES_NETWORK"
fi
if [[ -n "$REDIS_IMAGE" ]]; then
    validate_database_network "Redis exporter" "$REDIS_NETWORK"
fi

readonly INSTALL_DIR="/etc/aioms-exporters"
readonly SECRETS_DIR="$INSTALL_DIR/secrets"
readonly CONFIG_PATH="$INSTALL_DIR/compose.yaml"
readonly GROUP_NAME="aioms-exporters"
readonly TEMP_CONFIG="$(mktemp /tmp/aioms-exporters-compose.XXXXXX)"

cleanup() {
    rm -f -- "$TEMP_CONFIG"
}
trap cleanup EXIT

yaml_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '"%s"' "$value"
}

ensure_secret_group() {
    if ! getent group "$GROUP_NAME" >/dev/null 2>&1; then
        if command -v groupadd >/dev/null 2>&1; then
            groupadd --system "$GROUP_NAME"
        elif command -v addgroup >/dev/null 2>&1; then
            addgroup --system "$GROUP_NAME"
        else
            fail "无法创建 $GROUP_NAME 系统组"
        fi
    fi
    getent group "$GROUP_NAME" | cut -d: -f3
}

secret_group_id=""
if [[ -n "$MYSQL_IMAGE" || -n "$POSTGRES_IMAGE" || -n "$REDIS_PASSWORD_FILE" ]]; then
    secret_group_id="$(ensure_secret_group)"
    [[ "$secret_group_id" =~ ^[0-9]+$ ]] || fail "无法读取 $GROUP_NAME 组 ID"
fi

cat >"$TEMP_CONFIG" <<EOF
# 由 AIOMS scripts/target-server/metrics/install.sh 生成。
# 请修改 deploy.conf 和凭据文件后重新运行 deploy.sh，不要直接编辑。
services:
EOF

if [[ -n "$NODE_IMAGE" ]]; then
    cat >>"$TEMP_CONFIG" <<EOF
  node-exporter:
    image: $NODE_IMAGE
    container_name: aioms-node-exporter
    network_mode: host
    pid: host
    restart: unless-stopped
    read_only: true
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    command:
      - --path.rootfs=/host
      - --collector.processes
      - --collector.filesystem.mount-points-exclude=^/(dev|proc|sys|var/lib/docker/.+|var/lib/containers/storage/.+)(\$\$|/)
    volumes:
      - /:/host:ro,rslave
    labels:
      aioms.managed: "true"
      aioms.server: $(yaml_quote "$SERVER_NAME")
      aioms.environment: $(yaml_quote "$ENVIRONMENT")

EOF
fi

if [[ -n "$CADVISOR_IMAGE" ]]; then
    cat >>"$TEMP_CONFIG" <<EOF
  cadvisor:
    image: $CADVISOR_IMAGE
    container_name: aioms-cadvisor
    restart: unless-stopped
    privileged: true
    devices:
      - /dev/kmsg:/dev/kmsg
    ports:
      - "8080:8080"
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - $(yaml_quote "$DOCKER_ROOT_DIR:$DOCKER_ROOT_DIR:ro")
      - /dev/disk:/dev/disk:ro
    labels:
      aioms.managed: "true"
      aioms.server: $(yaml_quote "$SERVER_NAME")
      aioms.environment: $(yaml_quote "$ENVIRONMENT")

EOF
fi

if [[ -n "$MYSQL_IMAGE" ]]; then
    cat >>"$TEMP_CONFIG" <<EOF
  mysql-exporter:
    image: $MYSQL_IMAGE
    container_name: aioms-mysql-exporter
EOF
    if [[ "$MYSQL_NETWORK" == "host" ]]; then
        cat >>"$TEMP_CONFIG" <<EOF
    network_mode: host
EOF
    else
        cat >>"$TEMP_CONFIG" <<EOF
    networks:
      - mysql-target
    ports:
      - "9104:9104"
EOF
    fi
    cat >>"$TEMP_CONFIG" <<EOF
    restart: unless-stopped
    read_only: true
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    group_add:
      - "$secret_group_id"
    command:
      - --config.my-cnf=/.my.cnf
    volumes:
      - $SECRETS_DIR/mysql-exporter.my.cnf:/.my.cnf:ro
    labels:
      aioms.managed: "true"
      aioms.server: $(yaml_quote "$SERVER_NAME")
      aioms.environment: $(yaml_quote "$ENVIRONMENT")

EOF
fi

if [[ -n "$POSTGRES_IMAGE" ]]; then
    cat >>"$TEMP_CONFIG" <<EOF
  postgres-exporter:
    image: $POSTGRES_IMAGE
    container_name: aioms-postgres-exporter
EOF
    if [[ "$POSTGRES_NETWORK" == "host" ]]; then
        cat >>"$TEMP_CONFIG" <<EOF
    network_mode: host
EOF
    else
        cat >>"$TEMP_CONFIG" <<EOF
    networks:
      - postgres-target
    ports:
      - "9187:9187"
EOF
    fi
    cat >>"$TEMP_CONFIG" <<EOF
    restart: unless-stopped
    read_only: true
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    group_add:
      - "$secret_group_id"
    environment:
      DATA_SOURCE_URI: $(yaml_quote "$POSTGRES_URI")
      DATA_SOURCE_USER: $(yaml_quote "$POSTGRES_USER")
      DATA_SOURCE_PASS_FILE: /.postgres-exporter.password
    volumes:
      - $SECRETS_DIR/postgres-exporter.password:/.postgres-exporter.password:ro
    labels:
      aioms.managed: "true"
      aioms.server: $(yaml_quote "$SERVER_NAME")
      aioms.environment: $(yaml_quote "$ENVIRONMENT")

EOF
fi

if [[ -n "$REDIS_IMAGE" ]]; then
    cat >>"$TEMP_CONFIG" <<EOF
  redis-exporter:
    image: $REDIS_IMAGE
    container_name: aioms-redis-exporter
EOF
    if [[ "$REDIS_NETWORK" == "host" ]]; then
        cat >>"$TEMP_CONFIG" <<EOF
    network_mode: host
EOF
    else
        cat >>"$TEMP_CONFIG" <<EOF
    networks:
      - redis-target
    ports:
      - "9121:9121"
EOF
    fi
    cat >>"$TEMP_CONFIG" <<EOF
    restart: unless-stopped
    read_only: true
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
EOF
    if [[ -n "$REDIS_PASSWORD_FILE" ]]; then
        cat >>"$TEMP_CONFIG" <<EOF
    group_add:
      - "$secret_group_id"
EOF
    fi
    cat >>"$TEMP_CONFIG" <<EOF
    environment:
      REDIS_ADDR: $(yaml_quote "$REDIS_ADDRESS")
      REDIS_USER: $(yaml_quote "$REDIS_USER")
EOF
    if [[ -n "$REDIS_PASSWORD_FILE" ]]; then
        cat >>"$TEMP_CONFIG" <<EOF
      REDIS_PASSWORD_FILE: /.redis-exporter.password
    volumes:
      - $SECRETS_DIR/redis-exporter.password:/.redis-exporter.password:ro
EOF
    fi
    cat >>"$TEMP_CONFIG" <<EOF
    labels:
      aioms.managed: "true"
      aioms.server: $(yaml_quote "$SERVER_NAME")
      aioms.environment: $(yaml_quote "$ENVIRONMENT")

EOF
fi

if [[ \
    ( -n "$MYSQL_IMAGE" && "$MYSQL_NETWORK" != "host" ) || \
    ( -n "$POSTGRES_IMAGE" && "$POSTGRES_NETWORK" != "host" ) || \
    ( -n "$REDIS_IMAGE" && "$REDIS_NETWORK" != "host" ) \
]]; then
    cat >>"$TEMP_CONFIG" <<EOF
networks:
EOF
    if [[ -n "$MYSQL_IMAGE" && "$MYSQL_NETWORK" != "host" ]]; then
        cat >>"$TEMP_CONFIG" <<EOF
  mysql-target:
    external: true
    name: $(yaml_quote "$MYSQL_NETWORK")
EOF
    fi
    if [[ -n "$POSTGRES_IMAGE" && "$POSTGRES_NETWORK" != "host" ]]; then
        cat >>"$TEMP_CONFIG" <<EOF
  postgres-target:
    external: true
    name: $(yaml_quote "$POSTGRES_NETWORK")
EOF
    fi
    if [[ -n "$REDIS_IMAGE" && "$REDIS_NETWORK" != "host" ]]; then
        cat >>"$TEMP_CONFIG" <<EOF
  redis-target:
    external: true
    name: $(yaml_quote "$REDIS_NETWORK")
EOF
    fi
fi

install -d -o root -g root -m 0750 "$INSTALL_DIR"
if [[ -n "$secret_group_id" ]]; then
    install -d -o root -g "$GROUP_NAME" -m 0750 "$SECRETS_DIR"
fi
if [[ -n "$MYSQL_IMAGE" ]]; then
    install -o root -g "$GROUP_NAME" -m 0640 \
        "$MYSQL_CONFIG" "$SECRETS_DIR/mysql-exporter.my.cnf"
fi
if [[ -n "$POSTGRES_IMAGE" ]]; then
    install -o root -g "$GROUP_NAME" -m 0640 \
        "$POSTGRES_PASSWORD_FILE" "$SECRETS_DIR/postgres-exporter.password"
fi
if [[ -n "$REDIS_PASSWORD_FILE" ]]; then
    install -o root -g "$GROUP_NAME" -m 0640 \
        "$REDIS_PASSWORD_FILE" "$SECRETS_DIR/redis-exporter.password"
fi

"${COMPOSE[@]}" --project-name aioms-exporters -f "$TEMP_CONFIG" config >/dev/null

if [[ -f "$CONFIG_PATH" ]]; then
    backup_path="$CONFIG_PATH.backup.$(date +%Y%m%d%H%M%S)"
    cp -a -- "$CONFIG_PATH" "$backup_path"
    echo "原指标配置已备份：$backup_path"
fi
install -o root -g root -m 0640 "$TEMP_CONFIG" "$CONFIG_PATH"

if [[ "$START_EXPORTERS" != true ]]; then
    echo "Exporter 配置已安装但未启动。审核后运行："
    echo "  ${COMPOSE[*]} --project-name aioms-exporters -f $CONFIG_PATH up -d --remove-orphans"
    exit 0
fi

canonical_image_name() {
    local image="$1"
    local first_component="${image%%/*}"

    if [[ "$image" != */* ]]; then
        printf 'docker.io/library/%s' "$image"
    elif [[ "$first_component" == *.* || "$first_component" == *:* ||
        "$first_component" == "localhost" ]]; then
        printf '%s' "$image"
    else
        printf 'docker.io/%s' "$image"
    fi
}

pull_exporter_image() {
    local original_image="$1"
    local mirrored_image

    if [[ -z "$IMAGE_MIRROR_PREFIX" ]]; then
        docker pull "$original_image"
        return
    fi
    if [[ "$original_image" == *@* ]]; then
        fail "镜像代理暂不支持 digest 引用，请传空 --image-mirror-prefix 后直连：$original_image"
    fi

    mirrored_image="$IMAGE_MIRROR_PREFIX/$(canonical_image_name "$original_image")"
    echo "临时拉取 exporter 镜像：$mirrored_image"
    docker pull "$mirrored_image"
    if ! docker tag "$mirrored_image" "$original_image"; then
        docker image rm "$mirrored_image" >/dev/null 2>&1 || true
        fail "无法把代理镜像标记为原始名称：$original_image"
    fi
    docker image rm "$mirrored_image" >/dev/null
}

for image in \
    "$NODE_IMAGE" "$CADVISOR_IMAGE" "$MYSQL_IMAGE" "$POSTGRES_IMAGE" "$REDIS_IMAGE"; do
    [[ -z "$image" ]] || pull_exporter_image "$image"
done

"${COMPOSE[@]}" --project-name aioms-exporters -f "$CONFIG_PATH" up -d --remove-orphans
"${COMPOSE[@]}" --project-name aioms-exporters -f "$CONFIG_PATH" ps

echo "目标服务器指标 exporter 已部署。下一步运行同目录 check.sh。"
