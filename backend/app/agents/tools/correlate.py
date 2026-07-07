"""关联原语（方向一·任务5）：把三支柱在同一实体、同一时间窗对齐。

给定一台服务器（业务名）+ 一个时刻，**并发**拉齐它的指标走势 + 日志 + 正在触发的告警，
并置成一份"横切面报告"，便于一眼看清"那会儿这台机器发生了什么"——这是 AI SRE 区别于
普通 chatbot 的关键：不是逐个支柱单独问，而是把它们在同一实体、同一窗口对齐一起看。

实体怎么映射到监控标签：靠 servers 表的 monitor_labels 列（{"instance": ..., "service": ...}）。
correlate_incident 收业务服务器名 → 查表拿到监控标签 → 分别去 Prometheus / Loki / Alertmanager
拉数据。关联的"聪明"（判断因果）留给 LLM，这里只干"拉齐 + 裁剪 + 并置"这件确定性的活。
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from datetime import datetime, timedelta

from langchain_core.tools import tool

from app.agents.tools.metrics import PROMQL_TEMPLATES
from app.db.session import AsyncSessionLocal
from app.services import server_service
from app.services.observability_service import (
    am_get_alerts,
    loki_query_range,
    prom_query_range,
)

# 关联报告里日志最多采样几条（横切面重在"概览"，不是把日志全铺出来）
_LOG_SAMPLE = 12
# 日志按窗口拉取的上限
_LOG_FETCH_LIMIT = 300
_MAX_LINE_LEN = 300


@tool
async def correlate_incident(server: str, at: str = "", window_minutes: int = 15) -> str:
    """把一台服务器的指标、日志、告警在同一时间窗对齐，生成横切面报告，排查"那会儿发生了什么"。

    适合"某台机器某个时刻出了问题，一起看看它的指标/日志/告警"这类排查场景（比逐个调
    query_metrics / search_logs / query_alerts 更省事、且时间对齐）。

    - server：业务服务器名（servers 表里的 name）。需要这台服务器配了 monitor_labels（监控标签映射）。
    - at：时间锚点，ISO8601（如 "2026-07-07T14:00"，不带时区当本地时间）。留空=现在。
    - window_minutes：锚点前后各取多少分钟，默认 15（即总共 30 分钟窗口）。
    """
    # 1. 解析实体 → 监控标签
    async with AsyncSessionLocal() as db:
        servers = await server_service.list_servers(db)
    srv = next((s for s in servers if s.name == server), None)
    if srv is None:
        names = "、".join(s.name for s in servers) or "（无）"
        return f"没有找到名为 {server} 的服务器。当前有：{names}。"
    labels = srv.monitor_labels or {}
    instance = labels.get("instance")
    service = labels.get("service")
    if not instance and not service:
        return (
            f"{server} 还没有配置监控标签（monitor_labels），无法关联。"
            "需要给它配 instance（查 Prometheus 指标/告警）和/或 service（查 Loki 日志）。"
        )
    if window_minutes <= 0:
        return "window_minutes 需要是正数（表示锚点前后各取多少分钟）。"

    # 2. 时间窗
    anchor = _parse_at(at)
    start = anchor - timedelta(minutes=window_minutes)
    end = anchor + timedelta(minutes=window_minutes)

    # 3. 并发拉三支柱（互不依赖，gather 一起跑）
    metrics_text, logs_text, alerts_text = await asyncio.gather(
        _pull_metrics(instance, start, end),
        _pull_logs(service, start, end),
        _pull_alerts(instance),
    )

    # 4. 并置成一份报告
    return "\n".join(
        [
            f"## 关联横切面：{server} @ {_clock(anchor)}（窗口 {_clock(start)} ~ {_clock(end)}）",
            f"实体标签：instance={instance or '（无）'}　service={service or '（无）'}",
            "",
            "### 指标",
            metrics_text,
            "",
            "### 日志",
            logs_text,
            "",
            "### 告警（当前 firing）",
            alerts_text,
        ]
    )


async def _pull_metrics(instance: str | None, start: datetime, end: datetime) -> str:
    if not instance:
        return "（未配置 instance 标签，跳过指标）"
    start_s, end_s = start.timestamp(), end.timestamp()
    # 采样步长按窗口自适应：控制每条时序 ~200 点，大窗口也不会点数爆炸（Prometheus 有 11000 点上限）
    step = f"{max(30, int((end_s - start_s) / 200))}s"
    lines: list[str] = []
    for key, name in [("cpu", "CPU 使用率"), ("memory", "内存使用率")]:
        # 把网络调用 + 结果解析都放进 try：数据源返回结构畸形时也只降级成一行提示，
        # 绝不让异常冒泡（否则会崩掉整个 gather、连累另外两支柱一起挂）。
        try:
            series = await prom_query_range(PROMQL_TEMPLATES[key], start_s, end_s, step=step)
            s = next((x for x in series if x.get("metric", {}).get("instance") == instance), None)
            summary = _summarize_series(s.get("values", [])) if s else "窗口内无数据"
        except Exception as exc:  # noqa: BLE001 —— 网络错/畸形数据都转成给 LLM 读的文本
            summary = f"查询失败（{exc}）"
        lines.append(f"- {name}：{summary}")
    return "\n".join(lines) if lines else "（无指标）"


async def _pull_logs(service: str | None, start: datetime, end: datetime) -> str:
    if not service:
        return "（未配置 service 标签，跳过日志）"
    start_ns = int(start.timestamp() * 1e9)
    end_ns = int(end.timestamp() * 1e9)
    logql = f'{{service_name="{_esc(service)}"}}'
    # 网络调用 + 结果解析都放进 try，畸形响应也只降级成一行提示，不冒泡崩掉 gather。
    try:
        streams = await loki_query_range(logql, start_ns, end_ns, limit=_LOG_FETCH_LIMIT)
        rows: list[tuple[int, str]] = []
        for st in streams:
            for ts, line in st.get("values", []):
                rows.append((int(ts), line))
    except Exception as exc:  # noqa: BLE001 —— 网络错/畸形数据都转成给 LLM 读的文本
        return f"查询失败（{exc}）"
    if not rows:
        return "窗口内无日志"
    rows.sort(key=lambda r: r[0], reverse=True)
    # 级别粗统计（按行文本匹配，跟 search_logs 的思路一致）
    tally = Counter(_level_of(line) for _, line in rows)
    dist = "、".join(f"{k} {v}" for k, v in tally.most_common())
    sample = [f"[{_clock_ns(ts)}] {_clean(line)}" for ts, line in rows[:_LOG_SAMPLE]]
    head = f"窗口内 {len(rows)} 条（{dist}），采样最近 {min(len(rows), _LOG_SAMPLE)} 条："
    return head + "\n" + "\n".join(sample)


async def _pull_alerts(instance: str | None) -> str:
    # 说明：Alertmanager 给的是"当前"正在 firing 的告警，不是"窗口内历史"，所以标题写"当前 firing"。
    try:
        alerts = await am_get_alerts()
    except Exception as exc:  # noqa: BLE001
        return f"查询失败（{exc}）"
    active = [a for a in alerts if a.get("status", {}).get("state") == "active"]
    if instance:
        active = [a for a in active if a.get("labels", {}).get("instance") == instance]
    if not active:
        return "该实例当前没有正在触发的告警"
    lines = []
    for a in active:
        lb = a.get("labels", {})
        summary = a.get("annotations", {}).get("summary", "")
        lines.append(f"- [{lb.get('severity', '?')}] {lb.get('alertname', '?')}：{summary}")
    return "\n".join(lines)


def _summarize_series(values: list) -> str:
    """把一条时序（[[ts, "值"], ...]）概括成"起始→结束、区间、趋势"。cpu/memory 都是百分比。"""
    nums: list[float] = []
    for _ts, v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not (math.isnan(f) or math.isinf(f)):
            nums.append(f)
    if not nums:
        return "无有效数据"
    first, last, lo, hi = nums[0], nums[-1], min(nums), max(nums)
    trend = "上升" if last > first + 1 else ("下降" if last < first - 1 else "平稳")
    return f"起始 {first:.1f}% → 结束 {last:.1f}%（区间 {lo:.1f}%~{hi:.1f}%，趋势{trend}）"


def _level_of(line: str) -> str:
    low = line.lower()
    if re.search(r"\berror\b", low):
        return "error"
    if re.search(r"\bwarn", low):
        return "warn"
    if re.search(r"\binfo\b", low):
        return "info"
    return "other"


def _parse_at(at: str) -> datetime:
    at = at.strip()
    if not at:
        return datetime.now().astimezone()  # 现在（本地时区）
    try:
        dt = datetime.fromisoformat(at)
    except ValueError:
        return datetime.now().astimezone()  # 解析不了就用现在
    if dt.tzinfo is None:
        dt = dt.astimezone()  # 无时区当本地时间
    return dt


def _clock(dt: datetime) -> str:
    return dt.astimezone().strftime("%m-%d %H:%M")


def _clock_ns(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9).strftime("%H:%M:%S")


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _clean(line: str) -> str:
    line = line.replace("\r", " ").replace("\n", " ")
    return line[:_MAX_LINE_LEN] + "…" if len(line) > _MAX_LINE_LEN else line
