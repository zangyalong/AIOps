"""Grafana 图表链接生成工具（方向一·任务4）。

跟别的工具不同：它**不查数据、不访问任何网络**，纯粹按给定的查询 + 时间窗，拼一个 Grafana
Explore 链接返回。用户点开就能在 Grafana 里看到对应的指标走势图 / 日志面板。

数据源 uid（prometheus / loki）是 docker/grafana/provisioning 里固定死的，URL 格式是对着
Grafana 13 实测通过的（Grafana 11+ 的 panes 参数格式）。
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

from langchain_core.tools import tool

from app.agents.tools.metrics import PROMQL_TEMPLATES
from app.core.config import settings

# kind -> (数据源 uid, 数据源 type)。uid 跟 provisioning 里配的一致。
# alerts 也走 prometheus——Prometheus 会为每条 pending/firing 告警生成一条 ALERTS 时序，
# 所以告警能"当指标看"，复用同一套 Explore 机制，不用把 Alertmanager 也接进 Grafana。
_DATASOURCE = {
    "metrics": ("prometheus", "prometheus"),
    "logs": ("loki", "loki"),
    "alerts": ("prometheus", "prometheus"),
}


_WHAT = {"metrics": "指标走势图", "logs": "日志面板", "alerts": "告警 firing 时间线"}


@tool
async def grafana_explore_link(kind: str, query: str = "", minutes: int = 60) -> str:
    """生成一个 Grafana Explore 图表链接，用户点开就能看指标走势图 / 日志面板 / 告警时间线。

    适合用户想"看走势 / 看图 / 可视化"、或想跳到 Grafana 进一步排查时用（想要文字结论就用
    query_metrics / search_logs / query_alerts）。

    - kind："metrics"（看指标，走 Prometheus）| "logs"（看日志，走 Loki）| "alerts"（看告警时间线）
    - query：
        - kind=metrics：传 PromQL；也可直接传 cpu / memory / disk / up，会自动换成对应 PromQL。
        - kind=logs：传 LogQL（如 {service_name="aioms-fake-app"}）；也可只传服务名会自动包成选择器；留空看所有服务。
        - kind=alerts：留空看所有正在触发的告警；也可传 critical / warning / info 只看某个级别。
    - minutes：图表时间窗，最近多少分钟，默认 60。
    """
    kind = kind.strip().lower()
    if kind not in _DATASOURCE:
        return f'kind 只能是 "metrics" / "logs" / "alerts"，收到的是 "{kind}"。'
    uid, ds_type = _DATASOURCE[kind]
    q = query.strip()

    if kind == "metrics":
        if not q:
            return "看指标图需要指定 query（cpu / memory / disk / up，或一段 PromQL）。"
        # cpu/memory/disk/up 换成模板 PromQL，否则当成用户直接给的 PromQL
        expr = PROMQL_TEMPLATES.get(q, q)
    elif kind == "logs":
        if not q:
            expr = '{service_name=~".+"}'  # 不指定就看所有服务
        elif "{" in q:
            expr = q  # 已经是 LogQL 选择器
        else:
            safe = q.replace("\\", "\\\\").replace('"', '\\"')
            expr = f'{{service_name="{safe}"}}'
    else:  # alerts —— 走 Prometheus 的 ALERTS 指标
        if not q:
            expr = 'ALERTS{alertstate="firing"}'
        elif q in ("critical", "warning", "info"):
            expr = f'ALERTS{{alertstate="firing", severity="{q}"}}'
        else:
            expr = q  # 逃生口：直接当 PromQL（如 ALERTS{alertname="Watchdog"}）

    url = _build_explore_url(uid, ds_type, expr, minutes)
    return f"已生成 Grafana {_WHAT[kind]}链接（最近 {minutes} 分钟，点开即可查看）：\n{url}"


def _build_explore_url(uid: str, ds_type: str, expr: str, minutes: int) -> str:
    """按 Grafana 11+ 的 Explore URL 格式（panes 参数）拼链接。已对着 Grafana 13 实测过。"""
    pane = {
        # pane 的 key 是任意 id，Grafana 只当它是这个 Explore 面板的标识
        "aioms": {
            "datasource": uid,
            "queries": [
                {
                    "refId": "A",
                    "expr": expr,
                    "datasource": {"type": ds_type, "uid": uid},
                }
            ],
            "range": {"from": f"now-{minutes}m", "to": "now"},
        }
    }
    params = {
        "schemaVersion": "1",
        "panes": json.dumps(pane, separators=(",", ":"), ensure_ascii=False),
        "orgId": "1",
    }
    return f"{settings.grafana_base_url}/explore?{urlencode(params)}"
