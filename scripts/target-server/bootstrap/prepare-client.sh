#!/bin/bash

set -euo pipefail

fail() {
    echo "prepare-client.sh: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：
  ./bootstrap/prepare-client.sh \
    --server web01 \
    [--host 192.0.2.10] \
    [--port 22] \
    [--user aioms] \
    [--key-dir /path/to/ssh] \
    [--generated-root /absolute/path]

脚本在 AIOMS 客户端生成一组 Ed25519 密钥。私钥只保存在 key-dir；
同时生成一个可单独交给服务器管理员的角色包：
  bootstrap/generated/<server>/

角色包只包含一个公钥、角色配置脚本、连接说明和 SHA-256 校验清单，
不包含私钥、部署配置、Alloy 配置或 exporter 凭据。
已有且匹配的密钥会复用，不会覆盖现有私钥。
EOF
}

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"

SERVER_NAME=""
SERVER_HOST=""
SSH_PORT="22"
AIOMS_USER="aioms"
KEY_DIR="$PROJECT_ROOT/ssh"
GENERATED_ROOT="$SCRIPT_DIR/generated"

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
        --user)
            (($# >= 2)) || fail "--user 缺少参数"
            AIOMS_USER="$2"
            shift 2
            ;;
        --key-dir)
            (($# >= 2)) || fail "--key-dir 缺少参数"
            KEY_DIR="$2"
            shift 2
            ;;
        --generated-root)
            (($# >= 2)) || fail "--generated-root 缺少参数"
            GENERATED_ROOT="$2"
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

readonly SERVER_PATTERN='^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'
readonly USER_PATTERN='^[a-z_][a-z0-9_-]*[$]?$'
[[ "$SERVER_NAME" =~ $SERVER_PATTERN ]] || fail "必须提供格式合法的 --server"
[[ "$AIOMS_USER" =~ $USER_PATTERN ]] || fail "AIOMS 用户名格式不合法"
[[ "$AIOMS_USER" != "root" ]] || fail "AIOMS SSH 用户不能使用 root"
[[ "$SSH_PORT" =~ ^[0-9]{1,5}$ ]] || fail "SSH 端口格式不合法"
((SSH_PORT >= 1 && SSH_PORT <= 65535)) || fail "SSH 端口必须在 1 到 65535 之间"
[[ "$KEY_DIR" == /* ]] || fail "--key-dir 必须是绝对路径"
[[ "$KEY_DIR" != "/" ]] || fail "--key-dir 不能是根目录"
[[ "$GENERATED_ROOT" == /* ]] || fail "--generated-root 必须是绝对路径"
[[ "$GENERATED_ROOT" != "/" ]] || fail "--generated-root 不能是根目录"

if [[ -z "$SERVER_HOST" ]]; then
    SERVER_HOST="$SERVER_NAME"
fi
[[ "$SERVER_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$ ]] ||
    fail "SSH 主机地址格式不合法"

command -v ssh-keygen >/dev/null 2>&1 || fail "客户端缺少 ssh-keygen"
command -v install >/dev/null 2>&1 || fail "客户端缺少 install 命令"

umask 077
install -d -m 0700 "$KEY_DIR"

readonly KEY_PATH="$KEY_DIR/$SERVER_NAME-$AIOMS_USER"
readonly GENERATED_DIR="$GENERATED_ROOT/$SERVER_NAME"

ensure_key_pair() {
    local private_path="$1"
    local comment="$2"

    if [[ -e "$private_path" || -e "$private_path.pub" ]]; then
        [[ -f "$private_path" && -f "$private_path.pub" ]] ||
            fail "密钥对不完整，请人工处理：$private_path"

        local derived_public stored_public
        derived_public="$(ssh-keygen -y -f "$private_path")"
        stored_public="$(awk 'NF { print $1 " " $2; exit }' "$private_path.pub")"
        [[ "$derived_public" == "$stored_public" ]] ||
            fail "已有私钥和公钥不匹配：$private_path"
        echo "复用已有密钥：$private_path"
    else
        ssh-keygen -q -t ed25519 -N "" -C "$comment" -f "$private_path"
        echo "已生成密钥：$private_path"
    fi

    chmod 0600 "$private_path"
    chmod 0644 "$private_path.pub"
}

ensure_key_pair "$KEY_PATH" "aioms@$SERVER_NAME"

install -d -m 0755 "$GENERATED_DIR"
install -m 0644 "$KEY_PATH.pub" "$GENERATED_DIR/aioms.pub"
install -m 0755 "$SCRIPT_DIR/server-bootstrap.sh" "$GENERATED_DIR/configure-role.sh"

cat >"$GENERATED_DIR/client-connection-info.txt" <<EOF
server_name=$SERVER_NAME
host=$SERVER_HOST
port=$SSH_PORT
ssh_user=$AIOMS_USER
ssh_key_filename=$(basename -- "$KEY_PATH")

把整个角色包目录复制到目标服务器（请把 <server-admin> 替换为管理员账号）：
scp -P $SSH_PORT -r "$GENERATED_DIR" <server-admin>@$SERVER_HOST:/tmp/aioms-role-$SERVER_NAME

管理员在目标服务器执行：
cd /tmp/aioms-role-$SERVER_NAME
sha256sum -c SHA256SUMS
sudo ./configure-role.sh --user $AIOMS_USER

AIOMS servers 表的 SSH 字段：
ssh_user=$AIOMS_USER
ssh_key_filename=$(basename -- "$KEY_PATH")

核对并记录服务器 host key 后，可手工验证：
ssh -p $SSH_PORT -i "$KEY_PATH" $AIOMS_USER@$SERVER_HOST 'id'
EOF
chmod 0644 "$GENERATED_DIR/client-connection-info.txt"

readonly MANIFEST_PATH="$GENERATED_DIR/SHA256SUMS"
checksum_files=(
    "aioms.pub"
    "configure-role.sh"
    "client-connection-info.txt"
)

if command -v sha256sum >/dev/null 2>&1; then
    (
        cd "$GENERATED_DIR"
        sha256sum "${checksum_files[@]}"
    ) >"$MANIFEST_PATH"
elif command -v shasum >/dev/null 2>&1; then
    (
        cd "$GENERATED_DIR"
        shasum -a 256 "${checksum_files[@]}"
    ) >"$MANIFEST_PATH"
else
    fail "客户端缺少 sha256sum 或 shasum"
fi
chmod 0644 "$MANIFEST_PATH"

if command -v sha256sum >/dev/null 2>&1; then
    manifest_digest="$(sha256sum "$MANIFEST_PATH" | awk '{print $1}')"
else
    manifest_digest="$(shasum -a 256 "$MANIFEST_PATH" | awk '{print $1}')"
fi

echo
echo "客户端准备完成。"
echo "私钥目录（不要上传）：$KEY_DIR"
echo "可复制的角色包：$GENERATED_DIR"
echo "SHA256SUMS 摘要（请通过独立渠道告诉管理员）：$manifest_digest"
echo "下一步只需把上面的角色包目录交给服务器管理员，不要上传私钥或整个 target-server 包。"
