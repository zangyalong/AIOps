#!/bin/bash

set -euo pipefail

fail() {
    echo "configure-roles.sh: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：
  sudo ./configure-roles.sh \
    [--readonly-user aioms] \
    [--deploy-user aioms-deploy]

请把 configure-roles.sh、readonly.pub、deploy.pub 和 SHA256SUMS 放在同一目录。
本脚本只完成首次 SSH 角色初始化：创建账号、安装公钥、生成固定部署入口和最小 sudoers。
它不会安装 AIOMS 运行包、服务发现网关、Alloy 或 exporter，也不会修改 sshd_config。
EOF
}

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly READONLY_PUBLIC_KEY="$SCRIPT_DIR/readonly.pub"
readonly DEPLOY_PUBLIC_KEY="$SCRIPT_DIR/deploy.pub"
readonly CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS"
readonly INSTALL_ROOT="/opt/aioms-target-server"
readonly DEPLOY_GATEWAY="/usr/local/sbin/aioms-target-deploy"
readonly DEPLOY_SUDOERS="/etc/sudoers.d/aioms-deploy"
readonly DEPLOY_STATE_DIR="/var/lib/aioms-deploy"
readonly DEPLOY_INCOMING_DIR="$DEPLOY_STATE_DIR/incoming"

READONLY_USER="aioms"
DEPLOY_USER="aioms-deploy"

while (($# > 0)); do
    case "$1" in
        --readonly-user)
            (($# >= 2)) || fail "--readonly-user 缺少参数"
            READONLY_USER="$2"
            shift 2
            ;;
        --deploy-user)
            (($# >= 2)) || fail "--deploy-user 缺少参数"
            DEPLOY_USER="$2"
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

readonly USER_PATTERN='^[a-z_][a-z0-9_-]*[$]?$'
[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ "$READONLY_USER" =~ $USER_PATTERN ]] || fail "只读用户名格式不合法"
[[ "$DEPLOY_USER" =~ $USER_PATTERN ]] || fail "部署用户名格式不合法"
[[ "$READONLY_USER" != "$DEPLOY_USER" ]] || fail "只读用户和部署用户不能相同"
[[ "$READONLY_USER" != "root" && "$DEPLOY_USER" != "root" ]] ||
    fail "AIOMS SSH 用户不能使用 root"

for required_command in awk cut getent id install mktemp sed ssh-keygen stat visudo; do
    command -v "$required_command" >/dev/null 2>&1 || fail "服务器缺少命令：$required_command"
done

[[ -f "$READONLY_PUBLIC_KEY" ]] || fail "找不到只读公钥：$READONLY_PUBLIC_KEY"
[[ -f "$DEPLOY_PUBLIC_KEY" ]] || fail "找不到部署公钥：$DEPLOY_PUBLIC_KEY"

if [[ -f "$CHECKSUM_FILE" ]]; then
    command -v sha256sum >/dev/null 2>&1 || fail "服务器缺少 sha256sum，无法校验角色包"
    (
        cd "$SCRIPT_DIR"
        sha256sum -c SHA256SUMS
    )
else
    fail "找不到校验清单：$CHECKSUM_FILE"
fi

TEMP_PATHS=()
TEMP_PATH=""

cleanup() {
    local path
    for path in "${TEMP_PATHS[@]}"; do
        if [[ -n "$path" && -e "$path" ]]; then
            rm -f -- "$path"
        fi
    done
}
trap cleanup EXIT

make_temp() {
    TEMP_PATH="$(mktemp "$1")"
    TEMP_PATHS+=("$TEMP_PATH")
}

ensure_user() {
    local user_name="$1"
    if id "$user_name" >/dev/null 2>&1; then
        echo "复用已有用户：$user_name"
        echo "注意：本脚本不会移除该用户原有的用户组、SSH 密钥或其他 sudoers 权限。"
        return
    fi

    if command -v useradd >/dev/null 2>&1; then
        useradd --create-home --shell /bin/bash "$user_name"
    elif command -v adduser >/dev/null 2>&1; then
        adduser --disabled-password --gecos "" "$user_name"
    else
        fail "服务器缺少 useradd/adduser，无法创建 $user_name"
    fi
    echo "已创建用户：$user_name"
}

install_authorized_key() {
    local user_name="$1"
    local public_key_path="$2"
    local key_label="$3"

    ssh-keygen -lf "$public_key_path" >/dev/null 2>&1 ||
        fail "$key_label 公钥格式不合法"

    local public_key_line key_type key_blob unused
    public_key_line="$(awk '
        NF && $1 !~ /^#/ { print; count += 1 }
        END { if (count != 1) exit 1 }
    ' "$public_key_path")" || fail "$key_label 公钥必须且只能包含一条非空密钥"
    read -r key_type key_blob unused <<<"$public_key_line"
    [[ "$key_type" == "ssh-ed25519" && -n "$key_blob" ]] ||
        fail "$key_label 只接受 Ed25519 公钥"

    local user_home user_group authorized_keys temp_keys
    user_home="$(getent passwd "$user_name" | cut -d: -f6)"
    user_group="$(id -gn "$user_name")"
    [[ "$user_home" == /* && "$user_home" != "/" ]] ||
        fail "无法确定 $user_name 的安全家目录"

    install -d -o "$user_name" -g "$user_group" -m 0700 "$user_home/.ssh"
    authorized_keys="$user_home/.ssh/authorized_keys"

    make_temp "/tmp/aioms-authorized-keys.XXXXXX"
    temp_keys="$TEMP_PATH"
    if [[ -f "$authorized_keys" ]]; then
        awk -v key_blob="$key_blob" 'index($0, key_blob) == 0 { print }' \
            "$authorized_keys" >"$temp_keys"
    fi
    printf '%s %s\n' \
        'no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty' \
        "$public_key_line" >>"$temp_keys"
    install -o "$user_name" -g "$user_group" -m 0600 "$temp_keys" "$authorized_keys"
    rm -f -- "$temp_keys"
    echo "已安装 $key_label 公钥：$authorized_keys"
}

install_deploy_gateway() {
    local temp_gateway
    make_temp "/tmp/aioms-target-deploy.XXXXXX"
    temp_gateway="$TEMP_PATH"

    cat >"$temp_gateway" <<'EOF'
#!/bin/bash

set -euo pipefail

fail() {
    echo "aioms-target-deploy: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "该入口只能通过 sudo 运行"
[[ $# -eq 1 ]] || fail "只允许 apply 或 apply-alloy 固定子命令"

readonly INSTALL_DIR="/opt/aioms-target-server"
readonly DEPLOY_SCRIPT="$INSTALL_DIR/deploy.sh"
readonly CONFIG_PATH="$INSTALL_DIR/deploy.conf"
readonly ALLOY_APPLY_HELPER="/usr/local/libexec/aioms/apply-alloy-config"

verify_root_owned() {
    local path="$1"
    local label="$2"
    local owner mode
    owner="$(stat -c '%U' "$path")"
    mode="$(stat -c '%a' "$path")"
    [[ "$owner" == "root" ]] || fail "$label 必须由 root 持有"
    (( (8#$mode & 0022) == 0 )) || fail "$label 不能允许 group/other 写入"
}

case "$1" in
    apply)
        [[ -d "$INSTALL_DIR" && ! -L "$INSTALL_DIR" ]] ||
            fail "找不到 root 所有的固定部署目录：$INSTALL_DIR"
        [[ -f "$DEPLOY_SCRIPT" && -x "$DEPLOY_SCRIPT" && ! -L "$DEPLOY_SCRIPT" ]] ||
            fail "找不到 root 所有的固定部署脚本：$DEPLOY_SCRIPT"
        [[ -f "$CONFIG_PATH" && ! -L "$CONFIG_PATH" ]] ||
            fail "找不到 root 所有的固定部署配置：$CONFIG_PATH"
        verify_root_owned "$INSTALL_DIR" "部署目录"
        verify_root_owned "$DEPLOY_SCRIPT" "部署脚本"
        verify_root_owned "$CONFIG_PATH" "部署配置"
        exec "$DEPLOY_SCRIPT" --config "$CONFIG_PATH"
        ;;
    apply-alloy)
        [[ -f "$ALLOY_APPLY_HELPER" && -x "$ALLOY_APPLY_HELPER" && ! -L "$ALLOY_APPLY_HELPER" ]] ||
            fail "找不到 root 所有的 Alloy 应用器：$ALLOY_APPLY_HELPER"
        verify_root_owned "$ALLOY_APPLY_HELPER" "Alloy 应用器"
        exec "$ALLOY_APPLY_HELPER"
        ;;
    *)
        fail "只允许：sudo /usr/local/sbin/aioms-target-deploy apply 或 apply-alloy"
        ;;
esac
EOF

    install -o root -g root -m 0755 "$temp_gateway" "$DEPLOY_GATEWAY"
    echo "已生成固定部署入口：$DEPLOY_GATEWAY"
}

install_deploy_sudoers() {
    local temp_sudoers
    make_temp "/etc/sudoers.d/.aioms-deploy.XXXXXX"
    temp_sudoers="$TEMP_PATH"

    cat >"$temp_sudoers" <<EOF
# 由 AIOMS configure-roles.sh 生成。
# 部署账号只能应用管理员放在 /opt/aioms-target-server/ 下的 root 所有配置。
Cmnd_Alias AIOMS_TARGET_DEPLOY = /usr/local/sbin/aioms-target-deploy apply, \
    /usr/local/sbin/aioms-target-deploy apply-alloy

$DEPLOY_USER ALL=(root) NOPASSWD: AIOMS_TARGET_DEPLOY
EOF
    chmod 0440 "$temp_sudoers"
    visudo -cf "$temp_sudoers"
    install -o root -g root -m 0440 "$temp_sudoers" "$DEPLOY_SUDOERS"
    visudo -cf "$DEPLOY_SUDOERS"
    echo "已生成部署 sudoers：$DEPLOY_SUDOERS"
}

ensure_user "$READONLY_USER"
ensure_user "$DEPLOY_USER"
install_authorized_key "$READONLY_USER" "$READONLY_PUBLIC_KEY" "只读"
install_authorized_key "$DEPLOY_USER" "$DEPLOY_PUBLIC_KEY" "部署"

install -d -o root -g root -m 0755 "$INSTALL_ROOT"
deploy_group="$(id -gn "$DEPLOY_USER")"
install -d -o root -g root -m 0755 "$DEPLOY_STATE_DIR"
install -d -o "$DEPLOY_USER" -g "$deploy_group" -m 0700 "$DEPLOY_INCOMING_DIR"
install_deploy_gateway
install_deploy_sudoers

echo
echo "AIOMS SSH 角色初始化完成。"
echo "只读账号：$READONLY_USER"
echo "部署账号：$DEPLOY_USER"
echo "固定部署入口：sudo $DEPLOY_GATEWAY apply"
echo "Alloy 配置入口：sudo $DEPLOY_GATEWAY apply-alloy"
echo "部署暂存目录：$DEPLOY_INCOMING_DIR"
echo "部署目录已预留：$INSTALL_ROOT"
echo "注意：只读发现 sudoers 要由后续 discovery/install.sh 在安装网关时生成。"
echo "本脚本没有安装运行包、启动服务、修改 sshd_config 或修改防火墙。"
