#!/bin/bash

set -euo pipefail

fail() {
    echo "metrics/check.sh: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
command -v docker >/dev/null 2>&1 || fail "找不到 docker"

readonly CONFIG_PATH="/etc/aioms-exporters/compose.yaml"
[[ -f "$CONFIG_PATH" ]] || fail "找不到 $CONFIG_PATH，请先部署指标 exporter"

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    fail "找不到 Docker Compose"
fi

"${COMPOSE[@]}" --project-name aioms-exporters -f "$CONFIG_PATH" config >/dev/null
"${COMPOSE[@]}" --project-name aioms-exporters -f "$CONFIG_PATH" ps

if ! command -v curl >/dev/null 2>&1; then
    echo "未安装 curl，已跳过 HTTP /metrics 检查。"
    exit 0
fi

mapfile -t services < <(
    "${COMPOSE[@]}" --project-name aioms-exporters -f "$CONFIG_PATH" config --services
)

check_endpoint() {
    local service="$1"
    local url="$2"
    if curl -fsS --max-time 5 "$url" >/dev/null; then
        echo "正常：$service -> $url"
    else
        echo "异常：$service -> $url" >&2
        return 1
    fi
}

failed=false
for service in "${services[@]}"; do
    case "$service" in
        node-exporter) check_endpoint "$service" "http://127.0.0.1:9100/metrics" || failed=true ;;
        cadvisor) check_endpoint "$service" "http://127.0.0.1:8080/metrics" || failed=true ;;
        mysql-exporter) check_endpoint "$service" "http://127.0.0.1:9104/metrics" || failed=true ;;
        postgres-exporter) check_endpoint "$service" "http://127.0.0.1:9187/metrics" || failed=true ;;
        redis-exporter) check_endpoint "$service" "http://127.0.0.1:9121/metrics" || failed=true ;;
    esac
done

[[ "$failed" != true ]] || fail "至少一个 exporter 端点检查失败"
echo "全部已启用 exporter 的本机 /metrics 端点正常。"
