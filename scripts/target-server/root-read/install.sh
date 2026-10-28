#!/bin/bash

set -euo pipefail

fail() {
    echo "root-read/install.sh: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
[[ $# -eq 1 ]] || fail "用法：./install.sh <AIOps SSH 用户名>"

readonly AIOPS_SSH_USER="$1"
readonly USER_PATTERN='^[a-z_][a-z0-9_-]*[$]?$'
[[ "$AIOPS_SSH_USER" =~ $USER_PATTERN ]] || fail "SSH 用户名格式不合法"
[[ "$AIOPS_SSH_USER" != "root" ]] || fail "SSH 用户不能是 root"
id "$AIOPS_SSH_USER" >/dev/null 2>&1 || fail "用户 $AIOPS_SSH_USER 不存在"

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly INSTALL_CONTEXT="/usr/local/share/aiops/root-read"
readonly STATE_DIR="/etc/aiops"
readonly IMAGE_ID_FILE="$STATE_DIR/root-read.image-id"
readonly SOURCE_HASH_FILE="$STATE_DIR/root-read.source-sha256"
readonly MASK_DIR="/var/lib/aiops/root-read/empty-dir"
readonly MASK_FILE="/var/lib/aiops/root-read/empty-file"
readonly MASKED_DEV_DIR="/var/lib/aiops/root-read/masked-dev"
readonly MASK_NOTICE="AIOPS_PATH_MASKED: 该宿主机路径已被安全策略遮蔽，不能判断为空或不存在。"
readonly SUDOERS_TARGET="/etc/sudoers.d/aiops-root-read"
readonly TEMP_SUDOERS="$(mktemp /etc/sudoers.d/.aiops-root-read.XXXXXX)"
readonly TEMP_STATE="$(mktemp /tmp/aiops-root-read-state.XXXXXX)"

cleanup() {
    rm -f -- "$TEMP_SUDOERS" "$TEMP_STATE"
}
trap cleanup EXIT

for required_command in docker install sha256sum visudo; do
    command -v "$required_command" >/dev/null 2>&1 || fail "系统缺少 $required_command"
done

readonly HOST_PYTHON="/usr/bin/python3.11"
[[ -x "$HOST_PYTHON" ]] ||
    fail "系统缺少 Python 3.11：请先执行 dnf install -y python3.11"
"$HOST_PYTHON" "$SCRIPT_DIR/host_mounts.py" >/dev/null ||
    fail "Python 3.11 无法运行宿主机挂载解析器"

docker_versions="$(
    docker version --format '{{.Client.Version}} {{.Server.Version}}' 2>/dev/null
)" \
    || fail "无法连接 Docker Engine"
read -r docker_client_version docker_server_version <<<"$docker_versions"
docker_client_major="${docker_client_version%%.*}"
docker_server_major="${docker_server_version%%.*}"
[[ "$docker_client_major" =~ ^[0-9]+$ && "$docker_client_major" -ge 25 ]] \
    || fail "安全子挂载模式要求 Docker CLI 25+，当前为 $docker_client_version"
[[ "$docker_server_major" =~ ^[0-9]+$ && "$docker_server_major" -ge 25 ]] \
    || fail "安全子挂载模式要求 Docker Engine 25+，当前为 $docker_server_version"

install -d -o root -g root -m 0755 \
    "$INSTALL_CONTEXT" \
    "$STATE_DIR" \
    /usr/local/libexec/aiops
install -d -o root -g root -m 0755 "$MASK_DIR"
install -d -o root -g root -m 0755 "$MASKED_DEV_DIR"
printf '%s\n' "$MASK_NOTICE" >"$TEMP_STATE"
install -o root -g root -m 0444 "$TEMP_STATE" "$MASK_DIR/AIOPS_PATH_MASKED.txt"
chmod 0555 "$MASK_DIR"
install -o root -g root -m 0444 "$TEMP_STATE" "$MASK_FILE"
# Docker 嵌套挂载需要目标先存在；这个空普通文件仅作为
# /host/dev/null 的挂载点，运行时会被真正的 null 字符设备覆盖。
: >"$TEMP_STATE"
install -o root -g root -m 0444 "$TEMP_STATE" "$MASKED_DEV_DIR/null"
chmod 0555 "$MASKED_DEV_DIR"
install -o root -g root -m 0644 "$SCRIPT_DIR/Dockerfile" "$INSTALL_CONTEXT/Dockerfile"
install -o root -g root -m 0555 "$SCRIPT_DIR/entrypoint.py" "$INSTALL_CONTEXT/entrypoint.py"
install -o root -g root -m 0555 \
    "$SCRIPT_DIR/host_mounts.py" \
    /usr/local/libexec/aiops/root-read-host-mounts.py
install -o root -g root -m 0755 "$SCRIPT_DIR/aiops-root-read" /usr/local/sbin/aiops-root-read

source_hash="$({
    sha256sum "$INSTALL_CONTEXT/Dockerfile"
    sha256sum "$INSTALL_CONTEXT/entrypoint.py"
} | sha256sum | awk '{print $1}')"

image_id=""
if [[ -r "$SOURCE_HASH_FILE" && -r "$IMAGE_ID_FILE" ]] \
    && [[ "$(tr -d '[:space:]' <"$SOURCE_HASH_FILE")" == "$source_hash" ]]; then
    image_id="$(tr -d '[:space:]' <"$IMAGE_ID_FILE")"
    if [[ ! "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]] \
        || ! docker image inspect "$image_id" >/dev/null 2>&1; then
        image_id=""
    fi
fi

if [[ -z "$image_id" ]]; then
    echo "构建 AIOps root 只读诊断镜像……"
    docker build \
        --pull \
        --tag aiops-root-read:local \
        --file "$INSTALL_CONTEXT/Dockerfile" \
        "$INSTALL_CONTEXT"
    image_id="$(docker image inspect --format '{{.Id}}' aiops-root-read:local)"
    [[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]] || fail "Docker 返回了异常镜像 ID"

    printf '%s\n' "$image_id" >"$TEMP_STATE"
    install -o root -g root -m 0444 "$TEMP_STATE" "$IMAGE_ID_FILE"
    printf '%s\n' "$source_hash" >"$TEMP_STATE"
    install -o root -g root -m 0444 "$TEMP_STATE" "$SOURCE_HASH_FILE"
else
    echo "root-read 镜像内容未变化，复用固定镜像：$image_id"
fi

sed "s/@AIOPS_SSH_USER@/$AIOPS_SSH_USER/g" \
    "$SCRIPT_DIR/aiops-root-read.sudoers" >"$TEMP_SUDOERS"
chmod 0440 "$TEMP_SUDOERS"
visudo -cf "$TEMP_SUDOERS"
install -o root -g root -m 0440 "$TEMP_SUDOERS" "$SUDOERS_TARGET"
visudo -cf "$SUDOERS_TARGET"

if test_output="$(printf '{"command":"printf aiops-root-read-ok","timeout":5}' \
    | /usr/local/sbin/aiops-root-read)"; then
    :
else
    test_status=$?
    fail "root-read 容器自检失败（退出码 $test_status）：${test_output:-无标准输出}"
fi
[[ "$test_output" == *'"output": "aiops-root-read-ok"'* ]] \
    || fail "root-read 容器自检返回异常：$test_output"

if host_test_output="$(printf '{"command":"test -r /etc/os-release 2>/dev/null && printf aiops-host-read-ok","timeout":5,"scope":"host"}' \
    | /usr/local/sbin/aiops-root-read)"; then
    :
else
    host_test_status=$?
    fail "宿主机只读语境自检失败（退出码 $host_test_status）：${host_test_output:-无标准输出}"
fi
[[ "$host_test_output" == *'"output": "aiops-host-read-ok"'* ]] \
    || fail "宿主机只读语境自检返回异常：$host_test_output"

echo "AIOps root 只读沙箱安装完成。"
echo "SSH 用户：$AIOPS_SSH_USER"
echo "固定镜像：$image_id"
echo "sudoers：$SUDOERS_TARGET"
