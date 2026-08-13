"""把 Prometheus / Loki 原始结果整理成服务详情页可展示的轻量快照。"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

from app.core.config import settings
from app.models.server import Server
from app.models.service_catalog import Service, ServiceInstance
from app.schemas.service_observability import (
    LogLevel,
    ServerObservabilityRead,
    ServiceLogRead,
    ServiceMetricPointRead,
    ServiceMetricRead,
    ServiceMetricSeriesRead,
    ServiceObservabilityRead,
)
from app.services.observability_service import (
    loki_query_range,
    prom_query,
    prom_query_range,
)
from app.services.server_metric_resolution_service import (
    resolve_server_host_metric_instance,
)

_LOG_LIMIT = 40
_MAX_LOG_LINE = 800


@dataclass(frozen=True)
class _MetricDefinition:
    key: str
    label: str
    unit: str
    promql: str


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _selector(labels: dict[str, str]) -> str:
    body = ",".join(
        f'{key}="{_escape_label(value)}"' for key, value in labels.items() if value
    )
    return "{" + body + "}"


def _metric_definitions(
    service: Service, instance: ServiceInstance, server: Server | None
) -> list[_MetricDefinition]:
    identity = f"{service.name} {instance.runtime_name or ''}".lower()
    instance_selector = (
        _selector({"instance": instance.metric_instance})
        if instance.metric_instance
        else ""
    )
    runtime_selector = _selector(
        {
            "server": server.name if server else "",
            "runtime_name": instance.runtime_name or "",
        }
    )
    service_selector = _selector(
        {
            "server": server.name if server else "",
            "service": service.name,
        }
    )
    exporter_selector = (
        instance_selector
        or (runtime_selector if "exporter" in (instance.runtime_name or "") else "")
        or service_selector
    )
    target_selector = exporter_selector
    native_selector = exporter_selector

    if "node-exporter" in identity:
        node_selector = instance_selector or runtime_selector
        return [
            _MetricDefinition(
                "target_up", "指标采集", "state", f"max(up{target_selector})"
            ),
            _MetricDefinition(
                "cpu_usage",
                "主机 CPU",
                "percent",
                "100 - (avg(rate(node_cpu_seconds_total"
                f'{node_selector[:-1]},mode="idle"}}[5m])) * 100)',
            ),
            _MetricDefinition(
                "memory_usage",
                "主机内存",
                "percent",
                f"(1 - (node_memory_MemAvailable_bytes{node_selector} / "
                f"node_memory_MemTotal_bytes{node_selector})) * 100",
            ),
        ]

    if "mysql" in identity:
        return [
            _MetricDefinition(
                "target_up", "指标采集", "state", f"max(up{target_selector})"
            ),
            _MetricDefinition(
                "mysql_up", "MySQL 连接", "state", f"max(mysql_up{native_selector})"
            ),
            _MetricDefinition(
                "mysql_connections",
                "当前连接",
                "count",
                f"sum(mysql_global_status_threads_connected{native_selector})",
            ),
        ]

    if "postgres" in identity:
        return [
            _MetricDefinition(
                "target_up", "指标采集", "state", f"max(up{target_selector})"
            ),
            _MetricDefinition(
                "pg_up", "PostgreSQL 连接", "state", f"max(pg_up{native_selector})"
            ),
            _MetricDefinition(
                "pg_connections",
                "当前连接",
                "count",
                f"sum(pg_stat_database_numbackends{native_selector})",
            ),
        ]

    if "redis" in identity:
        return [
            _MetricDefinition(
                "target_up", "指标采集", "state", f"max(up{target_selector})"
            ),
            _MetricDefinition(
                "redis_up", "Redis 连接", "state", f"max(redis_up{native_selector})"
            ),
            _MetricDefinition(
                "redis_clients",
                "客户端连接",
                "count",
                f"sum(redis_connected_clients{native_selector})",
            ),
            _MetricDefinition(
                "redis_memory",
                "Redis 内存",
                "bytes",
                f"sum(redis_memory_used_bytes{native_selector})",
            ),
        ]

    if instance.runtime_type == "systemd" and server and instance.runtime_name:
        systemd_selector = _selector(
            {
                "server": server.name,
                "id": f"/system.slice/{instance.runtime_name}",
            }
        )
        return [
            _MetricDefinition(
                "service_cpu",
                "服务 CPU",
                "percent",
                "sum(rate(container_cpu_usage_seconds_total"
                f"{systemd_selector}[5m])) * 100",
            ),
            _MetricDefinition(
                "service_memory",
                "服务内存",
                "bytes",
                f"sum(container_memory_working_set_bytes{systemd_selector})",
            ),
        ]

    if instance.runtime_type == "docker" and server and instance.runtime_name:
        container_selector = runtime_selector
        return [
            _MetricDefinition(
                "container_cpu",
                "容器 CPU",
                "percent",
                "sum(rate(container_cpu_usage_seconds_total"
                f"{container_selector}[5m])) * 100",
            ),
            _MetricDefinition(
                "container_memory",
                "容器内存",
                "bytes",
                f"sum(container_memory_working_set_bytes{container_selector})",
            ),
        ]

    if instance.metric_instance:
        return [
            _MetricDefinition(
                "target_up", "指标采集", "state", f"max(up{target_selector})"
            ),
            _MetricDefinition(
                "scrape_samples",
                "采集样本",
                "count",
                f"max(scrape_samples_scraped{target_selector})",
            ),
            _MetricDefinition(
                "scrape_duration",
                "采集耗时",
                "seconds",
                f"max(scrape_duration_seconds{target_selector})",
            ),
        ]
    return []


def _server_metric_definitions(metric_instance: str) -> list[_MetricDefinition]:
    instance = _escape_label(metric_instance)
    selector = f'{{instance="{instance}"}}'
    cpu_selector = f'{{instance="{instance}",mode="idle"}}'
    filesystem_selector = (
        f'{{instance="{instance}",fstype!~"tmpfs|overlay|squashfs|fuse.*|virtiofs.*",'
        'mountpoint!~"/run($|/)|/var/lib/docker($|/)"}'
    )
    return [
        _MetricDefinition("target_up", "指标采集", "state", f"max(up{selector})"),
        _MetricDefinition(
            "cpu_usage",
            "CPU 使用率",
            "percent",
            f"100 - (avg(rate(node_cpu_seconds_total{cpu_selector}[5m])) * 100)",
        ),
        _MetricDefinition(
            "memory_usage",
            "内存使用率",
            "percent",
            f"(1 - (node_memory_MemAvailable_bytes{selector} / "
            f"node_memory_MemTotal_bytes{selector})) * 100",
        ),
        _MetricDefinition(
            "disk_usage",
            "磁盘最高使用率",
            "percent",
            "max((1 - (node_filesystem_avail_bytes"
            f"{filesystem_selector} / node_filesystem_size_bytes"
            f"{filesystem_selector})) * 100)",
        ),
        _MetricDefinition(
            "load_1m", "1 分钟负载", "count", f"max(node_load1{selector})"
        ),
        _MetricDefinition(
            "uptime",
            "运行时长",
            "duration",
            f"max(time() - node_boot_time_seconds{selector})",
        ),
    ]


def _server_trend_definitions(metric_instance: str) -> list[_MetricDefinition]:
    definitions = _server_metric_definitions(metric_instance)
    trend_keys = {"cpu_usage", "memory_usage", "disk_usage"}
    return [definition for definition in definitions if definition.key in trend_keys]


async def _resolve_server_metric_instance(
    server: Server,
) -> tuple[str | None, str | None]:
    return await resolve_server_host_metric_instance(server)


def _first_finite_value(rows: list[dict]) -> float | None:
    for row in rows:
        raw_value = (row.get("value") or [None, None])[1]
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


async def _load_metrics(
    definitions: list[_MetricDefinition],
) -> tuple[list[ServiceMetricRead], str | None]:
    if not definitions:
        return [], "该实例尚未配置可匹配的 Prometheus 指标"
    responses = await asyncio.gather(
        *(prom_query(definition.promql) for definition in definitions),
        return_exceptions=True,
    )
    metrics: list[ServiceMetricRead] = []
    errors: list[str] = []
    for definition, response in zip(definitions, responses, strict=True):
        if isinstance(response, BaseException):
            errors.append(str(response))
            continue
        value = _first_finite_value(response)
        if value is None:
            continue
        metrics.append(
            ServiceMetricRead(
                key=definition.key,
                label=definition.label,
                value=value,
                unit=definition.unit,
            )
        )
    if metrics:
        return metrics, None
    if errors:
        return [], f"Prometheus 查询失败：{errors[0]}"
    return [], "当前没有匹配到指标数据，请检查实例标签或 Prometheus Target"


def _range_step(minutes: int) -> str:
    # 每条时序控制在约 240 个点，短时间窗最低 15 秒。
    return f"{max(15, math.ceil(minutes * 60 / 240))}s"


def _series_points(rows: list[dict]) -> list[ServiceMetricPointRead]:
    points: dict[float, float] = {}
    for row in rows:
        for raw_timestamp, raw_value in row.get("values") or []:
            try:
                timestamp = float(raw_timestamp)
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(timestamp) and math.isfinite(value):
                points[timestamp] = value
    return [
        ServiceMetricPointRead(
            timestamp=datetime.fromtimestamp(timestamp, tz=UTC),
            value=value,
        )
        for timestamp, value in sorted(points.items())
    ]


async def _load_metric_series(
    definitions: list[_MetricDefinition], minutes: int
) -> tuple[list[ServiceMetricSeriesRead], str | None]:
    end = datetime.now(UTC).timestamp()
    start = end - minutes * 60
    responses = await asyncio.gather(
        *(
            prom_query_range(
                definition.promql,
                start,
                end,
                step=_range_step(minutes),
            )
            for definition in definitions
        ),
        return_exceptions=True,
    )
    series: list[ServiceMetricSeriesRead] = []
    errors: list[str] = []
    for definition, response in zip(definitions, responses, strict=True):
        if isinstance(response, BaseException):
            errors.append(str(response))
            continue
        points = _series_points(response)
        if points:
            series.append(
                ServiceMetricSeriesRead(
                    key=definition.key,
                    label=definition.label,
                    unit=definition.unit,
                    points=points,
                )
            )
    if series:
        return series, None
    if errors:
        return [], f"Prometheus 趋势查询失败：{errors[0]}"
    return [], "当前时间范围没有匹配到趋势数据"


def _grafana_server_url(metric_instance: str, minutes: int) -> str:
    query = urlencode(
        {
            "orgId": "1",
            "var-instance": metric_instance,
            "from": f"now-{minutes}m",
            "to": "now",
        }
    )
    base_url = settings.grafana_base_url.rstrip("/")
    return f"{base_url}/d/aiops-server-overview/server-overview?{query}"


def _log_level(line: str) -> LogLevel:
    if re.search(r"\b(error|err|fatal|panic)\b", line, re.IGNORECASE):
        return "error"
    if re.search(r"\b(warn|warning)\b", line, re.IGNORECASE):
        return "warn"
    if re.search(r"\b(info|notice)\b", line, re.IGNORECASE):
        return "info"
    return "unknown"


async def _load_logs(
    instance: ServiceInstance, server: Server | None, minutes: int
) -> tuple[list[ServiceLogRead], str | None]:
    if not instance.log_service:
        return [], "该实例尚未配置 Loki 服务标签"
    labels = {"server": server.name if server else ""}
    service_name = instance.log_service
    selectors = [
        _selector({**labels, "service": service_name}),
        _selector({**labels, "service_name": service_name}),
        _selector({**labels, "container": service_name}),
    ]
    end_ns = int(datetime.now(UTC).timestamp() * 1e9)
    start_ns = end_ns - minutes * 60 * 10**9
    responses = await asyncio.gather(
        *(
            loki_query_range(
                selector,
                start_ns,
                end_ns,
                limit=_LOG_LIMIT,
                direction="backward",
            )
            for selector in selectors
        ),
        return_exceptions=True,
    )
    errors = [str(item) for item in responses if isinstance(item, BaseException)]
    streams = [item for item in responses if not isinstance(item, BaseException)]
    rows: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for group in streams:
        for stream in group:
            for raw_timestamp, raw_line in stream.get("values", []):
                try:
                    timestamp = int(raw_timestamp)
                except (TypeError, ValueError):
                    continue
                line = str(raw_line).replace("\x00", "")[:_MAX_LOG_LINE]
                key = (timestamp, line)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(key)
    rows.sort(key=lambda item: item[0], reverse=True)
    logs = [
        ServiceLogRead(
            timestamp=datetime.fromtimestamp(timestamp / 1e9, tz=UTC),
            level=_log_level(line),
            line=line,
        )
        for timestamp, line in rows[:_LOG_LIMIT]
    ]
    if logs:
        return logs, None
    if errors and len(errors) == len(responses):
        return [], f"Loki 查询失败：{errors[0]}"
    return [], f"最近 {minutes} 分钟没有匹配到日志"


async def get_instance_observability(
    *,
    service: Service,
    instance: ServiceInstance,
    server: Server | None,
    minutes: int,
) -> ServiceObservabilityRead:
    metrics, logs = await asyncio.gather(
        _load_metrics(_metric_definitions(service, instance, server)),
        _load_logs(instance, server, minutes),
    )
    metric_rows, metric_message = metrics
    log_rows, log_message = logs
    return ServiceObservabilityRead(
        instance_id=instance.id,
        range_minutes=minutes,
        metric_instance=instance.metric_instance,
        log_service=instance.log_service,
        metrics=metric_rows,
        logs=log_rows,
        metric_message=metric_message,
        log_message=log_message,
    )


async def get_server_observability(
    server: Server, minutes: int
) -> ServerObservabilityRead:
    metric_instance, resolution_message = await _resolve_server_metric_instance(server)
    if metric_instance is None:
        return ServerObservabilityRead(
            server_id=server.id,
            range_minutes=minutes,
            metric_instance=None,
            metrics=[],
            series=[],
            metric_message=resolution_message,
            series_message=resolution_message,
            checked_at=datetime.now(UTC),
        )

    metrics_result, series_result = await asyncio.gather(
        _load_metrics(_server_metric_definitions(metric_instance)),
        _load_metric_series(_server_trend_definitions(metric_instance), minutes),
    )
    metrics, metric_message = metrics_result
    series, series_message = series_result
    return ServerObservabilityRead(
        server_id=server.id,
        range_minutes=minutes,
        metric_instance=metric_instance,
        metrics=metrics,
        series=series,
        grafana_url=_grafana_server_url(metric_instance, minutes),
        metric_message=metric_message,
        series_message=series_message,
        checked_at=datetime.now(UTC),
    )
