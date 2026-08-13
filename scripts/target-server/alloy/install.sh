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
    --ssh-user aiops \
    --install-only \
    [--allow-docker-socket]

  # 管理员已有服务映射时直接安装配置
  sudo ./install.sh \
    --server web01 \
    --loki-url http://监控服务器:3100/loki/api/v1/push \
    --ssh-user aiops \
    --map ./services.map \
    [--allow-docker-socket] \
    [--no-start]

--install-only          只安装 Alloy 和固定配置应用器，不要求 services.map，不启动服务。
--allow-docker-socket   允许后续已审批配置采集 Docker 日志；该权限接近 Docker 控制权限。
--no-start              安装并校验已有映射，但不启动或重启 Alloy。
--apt-repo-url URL      APT 仓库地址；默认 https://apt.grafana.com。
--apt-gpg-key-url URL   APT 仓库签名公钥地址；默认 Grafana 官方地址。
--rpm-repo-url URL      DNF/YUM 仓库地址；默认使用清华 TUNA 镜像。
--rpm-gpg-key-url URL   DNF/YUM 仓库签名公钥地址；默认 Grafana 官方地址。
--package-proxy-url URL 仅本次安装下载使用的 HTTP(S) 代理；不会写入系统全局代理。
EOF
}

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly CONFIG_DIR="/etc/alloy"
readonly CONFIG_PATH="$CONFIG_DIR/config.alloy"
readonly AIOPS_CONFIG_DIR="/etc/aiops"
readonly TARGET_CONFIG="$AIOPS_CONFIG_DIR/alloy-target.conf"
readonly LIBEXEC_DIR="/usr/local/libexec/aiops"
readonly INSTALLED_GENERATOR="$LIBEXEC_DIR/generate-alloy-config"
readonly INSTALLED_APPLIER="$LIBEXEC_DIR/apply-alloy-config"
readonly DEPLOY_STATE_DIR="/var/lib/aiops"
readonly DEPLOY_INCOMING_DIR="$DEPLOY_STATE_DIR/incoming"

SERVER_NAME=""
LOKI_PUSH_URL=""
SSH_USER="aiops"
SERVICE_MAP=""
ALLOW_DOCKER_SOCKET=false
INSTALL_ONLY=false
START_SERVICE=true
APT_REPO_URL="https://apt.grafana.com"
APT_GPG_KEY_URL="https://apt.grafana.com/gpg-full.key"
RPM_REPO_URL="https://mirrors.tuna.tsinghua.edu.cn/grafana/yum/rpm"
RPM_GPG_KEY_URL="https://rpm.grafana.com/gpg.key"
PACKAGE_PROXY_URL=""

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
        --ssh-user)
            (($# >= 2)) || fail "--ssh-user 缺少参数"
            SSH_USER="$2"
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
        --apt-repo-url)
            (($# >= 2)) || fail "--apt-repo-url 缺少参数"
            APT_REPO_URL="$2"
            shift 2
            ;;
        --apt-gpg-key-url)
            (($# >= 2)) || fail "--apt-gpg-key-url 缺少参数"
            APT_GPG_KEY_URL="$2"
            shift 2
            ;;
        --rpm-repo-url)
            (($# >= 2)) || fail "--rpm-repo-url 缺少参数"
            RPM_REPO_URL="$2"
            shift 2
            ;;
        --rpm-gpg-key-url)
            (($# >= 2)) || fail "--rpm-gpg-key-url 缺少参数"
            RPM_GPG_KEY_URL="$2"
            shift 2
            ;;
        --package-proxy-url)
            (($# >= 2)) || fail "--package-proxy-url 缺少参数"
            PACKAGE_PROXY_URL="$2"
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
validate_http_url() {
    local key="$1"
    local value="$2"

    [[ "$value" =~ ^https?://[^[:space:]]+$ ]] ||
        fail "$key 必须是没有空格的 http(s) URL"
}

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ "$SERVER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$ ]] || fail "必须提供合法 --server"
[[ "$LOKI_PUSH_URL" =~ ^https?://[^[:space:]]+/loki/api/v1/push$ ]] ||
    fail "--loki-url 必须是 http(s)://.../loki/api/v1/push"
[[ "$SSH_USER" =~ $USER_PATTERN ]] || fail "--ssh-user 格式不合法"
[[ "$SSH_USER" != "root" ]] || fail "--ssh-user 不能是 root"
id "$SSH_USER" >/dev/null 2>&1 || fail "AIOps SSH 用户不存在：$SSH_USER"
validate_http_url "--apt-repo-url" "$APT_REPO_URL"
validate_http_url "--apt-gpg-key-url" "$APT_GPG_KEY_URL"
validate_http_url "--rpm-repo-url" "$RPM_REPO_URL"
validate_http_url "--rpm-gpg-key-url" "$RPM_GPG_KEY_URL"
if [[ -n "$PACKAGE_PROXY_URL" ]]; then
    validate_http_url "--package-proxy-url" "$PACKAGE_PROXY_URL"
fi

APT_REPO_URL="${APT_REPO_URL%/}"
RPM_REPO_URL="${RPM_REPO_URL%/}"

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
        local -a apt_proxy_args=()
        if [[ -n "$PACKAGE_PROXY_URL" ]]; then
            apt_proxy_args=(
                -o "Acquire::http::Proxy=$PACKAGE_PROXY_URL"
                -o "Acquire::https::Proxy=$PACKAGE_PROXY_URL"
            )
        fi

        apt-get "${apt_proxy_args[@]}" update
        apt-get "${apt_proxy_args[@]}" install -y ca-certificates gpg wget
        install -d -o root -g root -m 0755 /etc/apt/keyrings
        if [[ -n "$PACKAGE_PROXY_URL" ]]; then
            env \
                http_proxy="$PACKAGE_PROXY_URL" \
                https_proxy="$PACKAGE_PROXY_URL" \
                HTTP_PROXY="$PACKAGE_PROXY_URL" \
                HTTPS_PROXY="$PACKAGE_PROXY_URL" \
                wget -O /etc/apt/keyrings/grafana.asc "$APT_GPG_KEY_URL"
        else
            wget -O /etc/apt/keyrings/grafana.asc "$APT_GPG_KEY_URL"
        fi
        chmod 0644 /etc/apt/keyrings/grafana.asc
        printf '%s\n' \
            "deb [signed-by=/etc/apt/keyrings/grafana.asc] $APT_REPO_URL stable main" \
            >/etc/apt/sources.list.d/grafana.list
        apt-get "${apt_proxy_args[@]}" update
        apt-get "${apt_proxy_args[@]}" install -y alloy
        return
    fi

    if command -v dnf >/dev/null 2>&1; then
        local repo_path="/etc/yum.repos.d/grafana.repo"
        local repo_backup=""
        local repo_existed=false
        local repo_gpgcheck=0
        local -a dnf_proxy_args=()

        # 官方仓库提供 repomd.xml.asc；TUNA 等镜像通常只同步仓库元数据和已签名 RPM 包。
        # 自定义镜像只关闭元数据签名校验，RPM 包签名校验始终保持开启。
        if [[ "$RPM_REPO_URL" == "https://rpm.grafana.com" ]]; then
            repo_gpgcheck=1
        fi
        if [[ -n "$PACKAGE_PROXY_URL" ]]; then
            dnf_proxy_args=(--setopt="proxy=$PACKAGE_PROXY_URL")
        fi

        # 仓库文件只在安装期间生效。无论成功、失败还是被中断，子 Shell 的 EXIT trap
        # 都会恢复服务器原有配置，避免 AIOps 永久改变系统软件源。
        (
            cleanup_temporary_repo() {
                dnf -q --disablerepo='*' --enablerepo=grafana clean metadata \
                    >/dev/null 2>&1 || true
                rm -f -- "$repo_path"
                if [[ "$repo_existed" == true ]]; then
                    cp -a -- "$repo_backup" "$repo_path"
                fi
                [[ -z "$repo_backup" ]] || rm -f -- "$repo_backup"
            }
            trap cleanup_temporary_repo EXIT

            if [[ -e "$repo_path" || -L "$repo_path" ]]; then
                repo_backup="$(mktemp /tmp/aiops-grafana-repo.XXXXXX)"
                rm -f -- "$repo_backup"
                cp -a -- "$repo_path" "$repo_backup"
                repo_existed=true
            fi

            cat >"$repo_path" <<EOF
[grafana]
name=grafana
baseurl=$RPM_REPO_URL
repo_gpgcheck=$repo_gpgcheck
enabled=1
gpgcheck=1
gpgkey=$RPM_GPG_KEY_URL
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
EOF

            echo "临时使用 Alloy RPM 源：$RPM_REPO_URL"
            if [[ "$repo_gpgcheck" == 0 ]]; then
                echo "镜像不校验仓库元数据签名；Alloy RPM 包签名校验仍保持开启。"
            fi
            dnf "${dnf_proxy_args[@]}" \
                --disablerepo='*' --enablerepo=grafana clean metadata
            dnf "${dnf_proxy_args[@]}" --refresh install -y alloy
        )
        echo "Alloy 安装完成，已恢复服务器原有 DNF 仓库配置。"
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

install -d -o root -g root -m 0755 "$LIBEXEC_DIR" "$AIOPS_CONFIG_DIR" "$DEPLOY_STATE_DIR"
install -d -o "$SSH_USER" -g "$(id -gn "$SSH_USER")" -m 0700 "$DEPLOY_INCOMING_DIR"
install -o root -g root -m 0755 "$SCRIPT_DIR/generate-config.sh" "$INSTALLED_GENERATOR"
install -o root -g root -m 0755 "$SCRIPT_DIR/aiops-alloy-apply" "$INSTALLED_APPLIER"

temp_target_config="$(mktemp "$AIOPS_CONFIG_DIR/.alloy-target.XXXXXX")"
cleanup_target_config() {
    rm -f -- "$temp_target_config"
}
trap cleanup_target_config EXIT
cat >"$temp_target_config" <<EOF
SERVER_NAME=$SERVER_NAME
LOKI_PUSH_URL=$LOKI_PUSH_URL
ALLOW_DOCKER_SOCKET=$ALLOW_DOCKER_SOCKET
SSH_USER=$SSH_USER
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
    echo "服务注册后由 AIOps 预览并审批应用日志配置。"
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

generated_config="$(mktemp /tmp/aiops-alloy-config.XXXXXX)"
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
