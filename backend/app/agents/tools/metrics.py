"""Prometheus 指标查询工具（方向一·任务1）。

打 Prometheus 的 HTTP API 查实时时序指标。cpu/memory/disk/up 这几个常用场景内置了写好的
PromQL 模板（让模型直接手写 PromQL 容易写错标签名/函数，不稳），同时保留"直接传一段
PromQL"的逃生口给高级用法。
"""

from __future__ import annotations

import math

from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.models.server import Server
from app.services import server_service
from app.services.observability_service import prom_label_values, prom_query

# 常用指标的 PromQL 模板。用 node_exporter 的标准查法：
# - cpu：100 减去 idle 占比 = 使用率。avg by(instance) 会把 job 等其它标签聚合掉，
#   所以 cpu 结果的 metric 里只剩 instance 一个标签。
# - memory：1 减去可用内存占比。
# - disk：真实文件系统的使用率。fstype 排除 tmpfs/overlay 等伪文件系统，以及 macOS
#   Docker Desktop 会冒出来的 virtiofs/fuse/fakeowner 这类桥接挂载（真 Linux 上没有这些
#   fstype，排掉无副作用），避免结果里混进一堆无意义的挂载点。
# - up：target 是否存活（1=存活，0=抓取失败），排查"指标为空是不是 exporter 挂了"用。
PROMQL_TEMPLATES = {
    "cpu": '100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
    "memory": "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100",
    "disk": (
        '(1 - node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs|fuse.*|'
        'virtiofs.*|fakeowner|selfowner"} / node_filesystem_size_bytes) * 100'
    ),
    "up": "up",
}

# 值语义是百分比的指标，格式化时加 %
_PERCENT_METRICS = {"cpu", "memory", "disk"}

_TITLES = {
    "cpu": "CPU 使用率",
    "memory": "内存使用率",
    "disk": "磁盘使用率",
    "up": "监控目标存活状态",
}


async def _query_metrics_text(metric: str, instance: str = "") -> str:
    """执行 Prometheus 查询并格式化成 Agent 可直接引用的文本。"""
    metric = metric.strip()
    is_template = metric in PROMQL_TEMPLATES
    promql = PROMQL_TEMPLATES.get(metric, metric)  # 命中模板用模板，否则当原始 PromQL

    try:
        results = await prom_query(promql)
    except Exception as exc:  # noqa: BLE001 —— 网络/超时/查询语法错都转成给 LLM 读的文本
        return (
            f"查询 Prometheus 失败：{exc}。"
            "请确认 Prometheus 在跑（默认 http://localhost:9090），以及查询表达式是否正确。"
        )

    if not results:
        return (
            f'查询 "{metric}" 没有返回数据。可能是该指标/表达式在 Prometheus 里没有对应时序，'
            "或对应的 exporter 没在采集。可以先查 up 看监控目标是否存活。"
        )

    # instance 过滤只对内置模板做（模板都带 instance 标签）；逃生口的原始 PromQL 不动，
    # 高级用法自己在表达式里写标签过滤。
    if instance and is_template:
        results = [
            r for r in results if r.get("metric", {}).get("instance") == instance
        ]
        if not results:
            return (
                f'没有 instance="{instance}" 的 {metric} 数据，请确认实例名'
                "（可以先不带 instance 查一次，看有哪些实例）。"
            )

    is_percent = metric in _PERCENT_METRICS
    lines: list[str] = []
    for r in results:
        labels = r.get("metric", {})
        raw = (r.get("value") or [None, None])[1]
        # value 恒为字符串，且可能是 "NaN" / "+Inf" / "-Inf"，必须容错
        try:
            num = float(raw)
        except (TypeError, ValueError):
            num = math.nan
        who = _label_of(labels, metric)
        if math.isnan(num) or math.isinf(num):
            lines.append(f"- {who}：无有效数据（{raw}）")
        elif metric == "up":
            lines.append(f"- {who}：{'存活' if num == 1 else '抓取失败（可能宕机）'}")
        elif is_percent:
            lines.append(f"- {who}：{num:.1f}%")
        else:
            lines.append(f"- {who}：{num:g}")

    scope = f"（instance={instance}）" if instance else ""
    title = _TITLES.get(metric, f"PromQL 查询结果：{metric}")
    return f"{title}{scope}（实时，来自 Prometheus）：\n" + "\n".join(lines)


@tool
async def query_metrics(metric: str, instance: str = "") -> str:
    """按 Prometheus instance 查询实时指标，适合直接给出监控实例或原始 PromQL 的场景。

    查询已纳管的业务服务器时优先使用 query_server_metrics，它会自动完成服务器到 instance 的映射。
    metric 可传 cpu / memory / disk / up；其它字符串会当成原始 PromQL。instance 可选，格式如
    "node-exporter:9100"；不传时返回全部实例。
    """
    return await _query_metrics_text(metric, instance)


@tool
async def query_server_metrics(server: str, metric: str) -> str:
    """查询一台已纳管服务器的实时指标，一次完成服务器解析和 Prometheus 查询。

    - server：servers 表里的服务器名称或 IP，例如 "docker-test-01" 或 "127.0.0.1"
    - metric：cpu / memory / disk / up

    工具会优先使用 servers.monitor_labels.instance；未配置时自动按服务器 IP 匹配 Prometheus
    instance。因此查询业务服务器时直接调用本工具，不需要先调用 list_metric_instances。
    """
    identifier = server.strip()
    async with AsyncSessionLocal() as db:
        srv = await server_service.get_server_by_name_or_ip(db, identifier)
    if srv is None:
        return f'没有找到名称或 IP 为 "{identifier}" 的已纳管服务器。'

    instance, error = await _resolve_metric_instance(srv)
    if instance is None:
        return error or f"无法确定服务器 {srv.name} 对应的 Prometheus instance。"

    result = await _query_metrics_text(metric, instance)
    return (
        f"服务器映射：{srv.name} ({srv.ip_address}) → Prometheus instance={instance}\n"
        f"{result}"
    )


async def _resolve_metric_instance(server: Server) -> tuple[str | None, str | None]:
    """优先读显式标签；缺失时在一次工具调用内部按服务器 IP 完成实例发现。"""
    configured = str((server.monitor_labels or {}).get("instance") or "").strip()
    if configured:
        return configured, None

    try:
        instances = await prom_label_values("instance")
    except Exception as exc:  # noqa: BLE001 —— 工具要把数据源错误转成 Agent 可读文本
        return (
            None,
            f"服务器 {server.name} 未配置 instance，自动查询 Prometheus 实例失败：{exc}。",
        )

    ip = server.ip_address.strip()
    matches = [item for item in instances if item == ip or item.startswith(f"{ip}:")]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, (
            f"服务器 {server.name} 未配置 monitor_labels.instance，Prometheus 中也没有与 IP "
            f"{ip} 匹配的实例。请先配置监控标签。"
        )
    return None, (
        f"服务器 {server.name} 的 IP {ip} 匹配到多个 Prometheus 实例："
        f"{', '.join(sorted(matches))}。请配置 monitor_labels.instance 明确指定。"
    )


@tool
async def list_metric_instances() -> str:
    """列出当前 Prometheus 有指标可查的实例名（给 query_metrics 的 instance 参数选用）。

    不确定要看哪台实例、或用户报的名字拿不准时，先调这个看有哪些实例，再去 query_metrics 查。
    """
    try:
        instances = await prom_label_values("instance")
    except Exception as exc:  # noqa: BLE001 —— 转成给 LLM 读的文本
        return (
            f"查询 Prometheus 实例列表失败：{exc}。"
            "请确认 Prometheus 在跑（默认 http://localhost:9090）。"
        )
    if not instances:
        return "Prometheus 里当前没有任何实例的指标（可能采集还没上来）。"
    lines = "\n".join(f"- {s}" for s in sorted(instances))
    return (
        "当前 Prometheus 有指标可查的实例（可作为 query_metrics 的 instance 参数）：\n"
        + lines
    )


def _label_of(labels: dict, metric: str) -> str:
    """从标签里挑一个最能标识"这是谁"的展示名：优先 instance，其次 job，都没有就列全部标签。"""
    inst = labels.get("instance")
    if inst:
        # disk 一台机器多行，带上 mountpoint/device 区分是哪个文件系统
        if metric == "disk":
            extra = labels.get("mountpoint") or labels.get("device")
            if extra:
                return f"{inst} {extra}"
        return inst
    job = labels.get("job")
    if job:
        return job
    return ", ".join(f"{k}={v}" for k, v in labels.items()) or "（无标签）"
