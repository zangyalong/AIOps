#!/bin/bash

set -euo pipefail

fail() {
    echo "generate-config.sh: $*" >&2
    exit 1
}

[[ $# -eq 4 ]] || fail \
    "用法：./generate-config.sh <services.map> <server> <Loki push URL> <输出文件>"

readonly SERVICE_MAP="$1"
readonly SERVER_NAME="$2"
readonly LOKI_PUSH_URL="$3"
readonly OUTPUT_PATH="$4"

[[ -f "$SERVICE_MAP" ]] || fail "找不到服务映射文件：$SERVICE_MAP"
[[ "$SERVER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$ ]] || fail "server 格式不合法"
[[ "$LOKI_PUSH_URL" =~ ^https?://[^[:space:]]+/loki/api/v1/push$ ]] || fail \
    "Loki URL 必须是 http(s)://.../loki/api/v1/push"

readonly OUTPUT_DIR="$(dirname -- "$OUTPUT_PATH")"
[[ -d "$OUTPUT_DIR" ]] || fail "输出目录不存在：$OUTPUT_DIR"
readonly TEMP_OUTPUT="$(mktemp "$OUTPUT_DIR/.config.alloy.XXXXXX")"

cleanup() {
    rm -f -- "$TEMP_OUTPUT"
}
trap cleanup EXIT

alloy_escape() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '%s' "$value"
}

append_common_config() {
    local escaped_loki_url
    escaped_loki_url="$(alloy_escape "$LOKI_PUSH_URL")"
    cat >"$TEMP_OUTPUT" <<EOF
// 由 AIOps scripts/target-server/alloy/generate-config.sh 生成。
// 不要直接编辑本文件；请修改 services.map 后重新生成。

logging {
  level  = "info"
  format = "logfmt"
}

loki.write "central" {
  endpoint {
    url = "$escaped_loki_url"
  }
}
EOF
}

append_journal_source() {
    local component_id="$1"
    local service="$2"
    local environment="$3"
    local runtime_name="$4"
    local escaped_service escaped_environment escaped_runtime escaped_server
    escaped_service="$(alloy_escape "$service")"
    escaped_environment="$(alloy_escape "$environment")"
    escaped_runtime="$(alloy_escape "$runtime_name")"
    escaped_server="$(alloy_escape "$SERVER_NAME")"

    cat >>"$TEMP_OUTPUT" <<EOF

loki.source.journal "$component_id" {
  matches    = "_SYSTEMD_UNIT=$escaped_runtime"
  forward_to = [loki.write.central.receiver]
  labels = {
    service      = "$escaped_service",
    service_name = "$escaped_service",
    environment  = "$escaped_environment",
    server       = "$escaped_server",
    runtime_type = "systemd",
    runtime_name = "$escaped_runtime",
    log_source   = "journal",
  }
}
EOF
}

append_docker_discovery() {
    cat >>"$TEMP_OUTPUT" <<'EOF'

discovery.docker "local" {
  host             = "unix:///var/run/docker.sock"
  refresh_interval = "5s"
}
EOF
}

append_docker_source() {
    local component_id="$1"
    local service="$2"
    local environment="$3"
    local runtime_name="$4"
    local escaped_service escaped_environment escaped_runtime escaped_server docker_regex
    escaped_service="$(alloy_escape "$service")"
    escaped_environment="$(alloy_escape "$environment")"
    escaped_runtime="$(alloy_escape "$runtime_name")"
    escaped_server="$(alloy_escape "$SERVER_NAME")"
    docker_regex="${runtime_name//./\\.}"

    cat >>"$TEMP_OUTPUT" <<EOF

discovery.relabel "${component_id}_target" {
  targets = discovery.docker.local.targets

  rule {
    source_labels = ["__meta_docker_container_name"]
    regex         = "/$docker_regex"
    action        = "keep"
  }

  rule {
    target_label = "service"
    replacement  = "$escaped_service"
  }

  // service_name 是 AIOps 旧日志查询的迁移兼容标签。
  rule {
    target_label = "service_name"
    replacement  = "$escaped_service"
  }

  rule {
    target_label = "environment"
    replacement  = "$escaped_environment"
  }

  rule {
    target_label = "server"
    replacement  = "$escaped_server"
  }

  rule {
    target_label = "runtime_type"
    replacement  = "docker"
  }

  rule {
    target_label = "runtime_name"
    replacement  = "$escaped_runtime"
  }

  rule {
    target_label = "log_source"
    replacement  = "docker"
  }
}

loki.source.docker "$component_id" {
  host       = "unix:///var/run/docker.sock"
  targets    = discovery.relabel.${component_id}_target.output
  labels     = {}
  forward_to = [loki.write.central.receiver]
}
EOF
}

append_file_source() {
    local component_id="$1"
    local service="$2"
    local environment="$3"
    local runtime_type="$4"
    local runtime_name="$5"
    local log_path="$6"
    local escaped_service escaped_environment escaped_type escaped_runtime
    local escaped_server escaped_path
    escaped_service="$(alloy_escape "$service")"
    escaped_environment="$(alloy_escape "$environment")"
    escaped_type="$(alloy_escape "$runtime_type")"
    escaped_runtime="$(alloy_escape "$runtime_name")"
    escaped_server="$(alloy_escape "$SERVER_NAME")"
    escaped_path="$(alloy_escape "$log_path")"

    cat >>"$TEMP_OUTPUT" <<EOF

loki.source.file "$component_id" {
  targets = [
    {
      __path__      = "$escaped_path",
      "service"      = "$escaped_service",
      "service_name" = "$escaped_service",
      "environment"  = "$escaped_environment",
      "server"       = "$escaped_server",
      "runtime_type" = "$escaped_type",
      "runtime_name" = "$escaped_runtime",
      "log_source"   = "file",
    },
  ]
  forward_to = [loki.write.central.receiver]

  file_match {
    enabled     = true
    sync_period = "10s"
  }
}
EOF
}

append_common_config

line_number=0
source_count=0
docker_discovery_written=false
while IFS='|' read -r service environment runtime_type runtime_name log_source log_path extra ||
    [[ -n "${service:-}" ]]; do
    line_number=$((line_number + 1))
    log_path="${log_path%$'\r'}"

    [[ -z "${extra:-}" ]] || fail "第 $line_number 行列数超过 6"
    [[ -n "${service//[[:space:]]/}" ]] || continue
    [[ "$service" != \#* ]] || continue
    [[ "$service" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$ ]] ||
        fail "第 $line_number 行 service 格式不合法"
    [[ "$environment" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$ ]] ||
        fail "第 $line_number 行 environment 格式不合法"
    [[ "$runtime_type" =~ ^(systemd|docker|process)$ ]] ||
        fail "第 $line_number 行 runtime_type 只允许 systemd/docker/process"
    [[ -n "$runtime_name" && ${#runtime_name} -le 200 ]] ||
        fail "第 $line_number 行 runtime_name 不能为空且不能超过 200 字符"
    [[ "$log_source" =~ ^(journal|docker|file|none)$ ]] ||
        fail "第 $line_number 行 log_source 只允许 journal/docker/file/none"

    source_count=$((source_count + 1))
    component_id="source_${source_count}"
    case "$log_source" in
        journal)
            [[ "$runtime_type" == "systemd" ]] ||
                fail "第 $line_number 行只有 systemd 运行时能使用 journal"
            [[ "$runtime_name" =~ ^[A-Za-z0-9_.@:-]+[.]service$ ]] ||
                fail "第 $line_number 行 journal runtime_name 必须是准确的 .service 单元名"
            append_journal_source \
                "$component_id" "$service" "$environment" "$runtime_name"
            ;;
        docker)
            [[ "$runtime_type" == "docker" ]] ||
                fail "第 $line_number 行 docker 日志源必须对应 docker 运行时"
            [[ "$runtime_name" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$ ]] ||
                fail "第 $line_number 行 Docker 容器名格式不合法"
            if [[ "$docker_discovery_written" == false ]]; then
                append_docker_discovery
                docker_discovery_written=true
            fi
            append_docker_source \
                "$component_id" "$service" "$environment" "$runtime_name"
            ;;
        file)
            [[ "$log_path" == /* ]] ||
                fail "第 $line_number 行 file 日志源必须填写绝对 log_path"
            [[ "$log_path" != *".."* ]] ||
                fail "第 $line_number 行 log_path 不能包含 .."
            append_file_source \
                "$component_id" "$service" "$environment" \
                "$runtime_type" "$runtime_name" "$log_path"
            ;;
        none)
            source_count=$((source_count - 1))
            ;;
    esac
done <"$SERVICE_MAP"

((source_count > 0)) || fail "映射文件没有启用任何日志源"
mv -- "$TEMP_OUTPUT" "$OUTPUT_PATH"
trap - EXIT
echo "已生成 Alloy 配置：$OUTPUT_PATH（$source_count 个日志源）"
