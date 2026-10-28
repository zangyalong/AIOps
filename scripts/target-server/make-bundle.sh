#!/bin/bash

set -euo pipefail

fail() {
    echo "make-bundle.sh: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：
  ./make-bundle.sh \
    --server web01 \
    --host 192.0.2.10 \
    [--port 22] \
    [--config ./deploy.conf] \
    [--key-dir /absolute/path/to/ssh] \
    [--output-dir /absolute/path/to/dist] \
    [--config-assets-dir /absolute/path]

生成一个不含私钥的首次安装包。目标服务器管理员解压后只需执行：
  sudo ./install.sh

deploy.conf 中首次可保持 ALLOY_SERVICE_MAP=、START_ALLOY=false；服务发现和注册后，
AI 再生成并审批应用 Alloy 日志配置。
EOF
}

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

SERVER_NAME=""
SERVER_HOST=""
SSH_PORT="22"
CONFIG_PATH="$SCRIPT_DIR/deploy.conf"
KEY_DIR="$PROJECT_ROOT/ssh"
OUTPUT_DIR="$SCRIPT_DIR/dist"
CONFIG_ASSETS_DIR="$SCRIPT_DIR"

while (($# > 0)); do
    case "$1" in
        --server)
            (($# >= 2)) || fail "--server 缺少参数"
            SERVER_NAME="$2"
            shift 2
            ;;
        --host)
            (($# >= 2)) || fail "--host 缺少参数"
            SERVER_HOST="$2"
            shift 2
            ;;
        --port)
            (($# >= 2)) || fail "--port 缺少参数"
            SSH_PORT="$2"
            shift 2
            ;;
        --config)
            (($# >= 2)) || fail "--config 缺少参数"
            CONFIG_PATH="$2"
            shift 2
            ;;
        --key-dir)
            (($# >= 2)) || fail "--key-dir 缺少参数"
            KEY_DIR="$2"
            shift 2
            ;;
        --output-dir)
            (($# >= 2)) || fail "--output-dir 缺少参数"
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --config-assets-dir)
            (($# >= 2)) || fail "--config-assets-dir 缺少参数"
            CONFIG_ASSETS_DIR="$2"
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

[[ "$SERVER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$ ]] ||
    fail "必须提供合法 --server"
[[ -n "$SERVER_HOST" ]] || SERVER_HOST="$SERVER_NAME"
[[ "$SERVER_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$ ]] ||
    fail "--host 格式不合法"
[[ "$SSH_PORT" =~ ^[0-9]{1,5}$ ]] || fail "--port 格式不合法"
((SSH_PORT >= 1 && SSH_PORT <= 65535)) || fail "--port 必须在 1 到 65535 之间"
[[ -f "$CONFIG_PATH" ]] || fail "找不到部署配置：$CONFIG_PATH"
[[ "$KEY_DIR" == /* ]] || fail "--key-dir 必须是绝对路径"
[[ "$OUTPUT_DIR" == /* ]] || fail "--output-dir 必须是绝对路径"
[[ "$CONFIG_ASSETS_DIR" == /* ]] || fail "--config-assets-dir 必须是绝对路径"
[[ -d "$CONFIG_ASSETS_DIR" ]] || fail "找不到配置素材目录：$CONFIG_ASSETS_DIR"

CONFIG_PATH="$(cd -- "$(dirname -- "$CONFIG_PATH")" && pwd)/$(basename -- "$CONFIG_PATH")"

AIOPS_SSH_USER=""
CONFIG_SERVER_NAME=""
ALLOY_SERVICE_MAP=""
ENABLE_MYSQL_EXPORTER="false"
MYSQL_EXPORTER_CONFIG=""
ENABLE_POSTGRES_EXPORTER="false"
POSTGRES_EXPORTER_PASSWORD_FILE=""
ENABLE_REDIS_EXPORTER="false"
REDIS_EXPORTER_PASSWORD_FILE=""
SEEN_KEYS="|"

trim() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

line_number=0
while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    ((line_number += 1))
    line="${raw_line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *=* ]] || fail "配置文件第 $line_number 行不是 KEY=VALUE"
    key="$(trim "${line%%=*}")"
    value="$(trim "${line#*=}")"
    case "$key" in
        AIOPS_SSH_USER|SERVER_NAME|ALLOY_SERVICE_MAP|\
        ENABLE_MYSQL_EXPORTER|MYSQL_EXPORTER_CONFIG|ENABLE_POSTGRES_EXPORTER|\
        POSTGRES_EXPORTER_PASSWORD_FILE|ENABLE_REDIS_EXPORTER|REDIS_EXPORTER_PASSWORD_FILE)
            [[ "$SEEN_KEYS" != *"|$key|"* ]] || fail "配置项重复：$key"
            SEEN_KEYS+="$key|"
            ;;
    esac
    case "$key" in
        AIOPS_SSH_USER) AIOPS_SSH_USER="$value" ;;
        SERVER_NAME) CONFIG_SERVER_NAME="$value" ;;
        ALLOY_SERVICE_MAP) ALLOY_SERVICE_MAP="$value" ;;
        ENABLE_MYSQL_EXPORTER) ENABLE_MYSQL_EXPORTER="$value" ;;
        MYSQL_EXPORTER_CONFIG) MYSQL_EXPORTER_CONFIG="$value" ;;
        ENABLE_POSTGRES_EXPORTER) ENABLE_POSTGRES_EXPORTER="$value" ;;
        POSTGRES_EXPORTER_PASSWORD_FILE) POSTGRES_EXPORTER_PASSWORD_FILE="$value" ;;
        ENABLE_REDIS_EXPORTER) ENABLE_REDIS_EXPORTER="$value" ;;
        REDIS_EXPORTER_PASSWORD_FILE) REDIS_EXPORTER_PASSWORD_FILE="$value" ;;
    esac
done <"$CONFIG_PATH"

readonly USER_PATTERN='^[a-z_][a-z0-9_-]*[$]?$'
[[ "$AIOPS_SSH_USER" =~ $USER_PATTERN ]] || fail "deploy.conf 的 AIOPS_SSH_USER 不合法"
[[ "$AIOPS_SSH_USER" != "root" ]] || fail "AIOPS_SSH_USER 不能是 root"
[[ "$CONFIG_SERVER_NAME" == "$SERVER_NAME" ]] ||
    fail "deploy.conf 的 SERVER_NAME 必须与 --server 一致"

for boolean_value in \
    "$ENABLE_MYSQL_EXPORTER" \
    "$ENABLE_POSTGRES_EXPORTER" \
    "$ENABLE_REDIS_EXPORTER"; do
    [[ "$boolean_value" == "true" || "$boolean_value" == "false" ]] ||
        fail "exporter 开关只能是 true 或 false"
done

readonly BUNDLE_NAME="aiops-$SERVER_NAME"
stage_root="$(mktemp -d /tmp/aiops-bundle.XXXXXX)"
cleanup() {
    rm -rf -- "$stage_root"
}
trap cleanup EXIT

readonly GENERATED_ROOT="$stage_root/generated"
bash "$SCRIPT_DIR/bootstrap/prepare-client.sh" \
    --server "$SERVER_NAME" \
    --host "$SERVER_HOST" \
    --port "$SSH_PORT" \
    --user "$AIOPS_SSH_USER" \
    --key-dir "$KEY_DIR" \
    --generated-root "$GENERATED_ROOT"

readonly ROLE_SOURCE="$GENERATED_ROOT/$SERVER_NAME"

readonly BUNDLE_DIR="$stage_root/$BUNDLE_NAME"
readonly TARGET_COPY="$BUNDLE_DIR/target-server"
readonly ROLE_COPY="$BUNDLE_DIR/role"
install -d -m 0755 "$TARGET_COPY" "$ROLE_COPY"

package_tar="$stage_root/target-server.tar"
(
    cd "$SCRIPT_DIR"
    tar -cf "$package_tar" \
        --exclude='./bootstrap/generated' \
        --exclude='./dist' \
        --exclude='./deploy.conf' \
        --exclude='./alloy/services.map' \
        --exclude='./metrics/mysql-exporter.my.cnf' \
        --exclude='./metrics/postgres-exporter.password' \
        --exclude='./metrics/redis-exporter.password' \
        .
)
(
    cd "$TARGET_COPY"
    tar -xf "$package_tar"
)

# 源码可按普通文件检出；只在安装包副本中恢复脚本执行权限，配置文件权限不变。
while IFS= read -r -d '' script_path; do
    if [[ "$(head -n 1 -- "$script_path")" == '#!'* ]]; then
        chmod 0755 "$script_path"
    fi
done < <(find "$TARGET_COPY" -type f -print0)

install -m 0644 "$ROLE_SOURCE/aiops.pub" "$ROLE_COPY/aiops.pub"
install -m 0755 "$ROLE_SOURCE/configure-role.sh" "$ROLE_COPY/configure-role.sh"
install -m 0644 \
    "$ROLE_SOURCE/client-connection-info.txt" "$ROLE_COPY/client-connection-info.txt"
install -m 0644 "$ROLE_SOURCE/SHA256SUMS" "$ROLE_COPY/SHA256SUMS"
install -m 0600 "$CONFIG_PATH" "$TARGET_COPY/deploy.conf"

copy_configured_file() {
    local relative_path="$1"
    local label="$2"
    [[ -n "$relative_path" ]] || fail "$label 配置路径不能为空"
    [[ "$relative_path" != /* ]] || fail "$label 在一键包中必须使用相对路径"
    [[ "/$relative_path/" != *"/../"* ]] || fail "$label 路径不能包含 .."
    [[ "$relative_path" =~ ^[A-Za-z0-9_./-]+$ ]] || fail "$label 路径包含不支持的字符"
    [[ -f "$CONFIG_ASSETS_DIR/$relative_path" ]] ||
        fail "找不到 $label：$CONFIG_ASSETS_DIR/$relative_path"
    install -d -m 0755 "$TARGET_COPY/$(dirname -- "$relative_path")"
    install -m 0600 "$CONFIG_ASSETS_DIR/$relative_path" "$TARGET_COPY/$relative_path"
}

if [[ -n "$ALLOY_SERVICE_MAP" ]]; then
    copy_configured_file "$ALLOY_SERVICE_MAP" "Alloy 服务映射"
fi
if [[ "$ENABLE_MYSQL_EXPORTER" == "true" ]]; then
    copy_configured_file "$MYSQL_EXPORTER_CONFIG" "MySQL exporter 配置"
fi
if [[ "$ENABLE_POSTGRES_EXPORTER" == "true" ]]; then
    copy_configured_file "$POSTGRES_EXPORTER_PASSWORD_FILE" "PostgreSQL exporter 密码文件"
fi
if [[ "$ENABLE_REDIS_EXPORTER" == "true" && -n "$REDIS_EXPORTER_PASSWORD_FILE" ]]; then
    copy_configured_file "$REDIS_EXPORTER_PASSWORD_FILE" "Redis exporter 密码文件"
fi

cat >"$BUNDLE_DIR/install.sh" <<EOF
#!/bin/bash

set -euo pipefail

fail() {
    echo "aiops-target-install: \$*" >&2
    exit 1
}

[[ \$EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
readonly SCRIPT_DIR="\$(cd -- "\$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)"
command -v sha256sum >/dev/null 2>&1 || fail "服务器缺少 sha256sum"
(
    cd "\$SCRIPT_DIR"
    sha256sum -c SHA256SUMS
)

"\$SCRIPT_DIR/role/configure-role.sh" --user "$AIOPS_SSH_USER"
"\$SCRIPT_DIR/target-server/install-runtime.sh" "\$SCRIPT_DIR/target-server"
"/opt/aiops-target-server/deploy.sh" \
    --config "/opt/aiops-target-server/deploy.conf"

echo "AIOps 目标服务器首次安装完成。"
echo "现在可以在 AIOps 对话中发现并注册 $SERVER_NAME 上的服务。"
EOF
chmod 0755 "$BUNDLE_DIR/install.sh"

readonly BUNDLE_MANIFEST="$BUNDLE_DIR/SHA256SUMS"
if command -v sha256sum >/dev/null 2>&1; then
    checksum_command=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
    checksum_command=(shasum -a 256)
else
    fail "客户端缺少 sha256sum 或 shasum"
fi
(
    cd "$BUNDLE_DIR"
    while IFS= read -r file_path; do
        "${checksum_command[@]}" "$file_path"
    done < <(find . -type f ! -path './SHA256SUMS' | LC_ALL=C sort)
) >"$BUNDLE_MANIFEST"
chmod 0644 "$BUNDLE_MANIFEST"

install -d -m 0755 "$OUTPUT_DIR"
archive_path="$OUTPUT_DIR/$BUNDLE_NAME.tar.gz"
tar -czf "$archive_path" -C "$stage_root" "$BUNDLE_NAME"
archive_digest="$("${checksum_command[@]}" "$archive_path" | awk '{print $1}')"

echo
echo "首次安装包已生成：$archive_path"
echo "压缩包 SHA-256：$archive_digest"
echo "私钥仍只保存在：$KEY_DIR"
echo "目标服务器解压后执行：cd $BUNDLE_NAME && sudo ./install.sh"
