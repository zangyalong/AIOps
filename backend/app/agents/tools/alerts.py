"""告警查询工具（方向一·任务3）：查 Alertmanager 里当前正在触发（firing）的真实告警。

从原来查 alert_events 种子表，改成查 Alertmanager v2 API 的实时 firing 告警（只读）。
alert_events 表和 scripts/seed_alerts.py 先留着不动——方向二做「告警生命周期状态机
（FIRING/ACK/RESOLVED）」时，正好用它把 Alertmanager 拉来的告警落库、加 fingerprint 去重。
"""

from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool

from app.services.observability_service import am_get_alerts

# critical 在前，方便按严重程度阅读
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@tool
async def query_alerts(severity: str = "", instance: str = "") -> str:
    """查询当前正在触发（firing）的告警，数据来自 Alertmanager。

    - severity：按级别过滤（如 critical / warning / info）。不传则各级别都返回。
    - instance：按实例过滤（如 "node-exporter:9100"）。不传则所有实例。
    只返回 active（正在烧）、未被静默/抑制的告警。
    """
    try:
        alerts = await am_get_alerts()
    except Exception as exc:  # noqa: BLE001 —— 网络/超时等都转成给 LLM 读的文本
        return (
            f"查询 Alertmanager 失败：{exc}。"
            "请确认 Alertmanager 在跑（默认 http://localhost:9093）。"
        )

    # 二次保险：只留 state=active（am_get_alerts 已用 active=true 请求，这里再兜一层）
    alerts = [a for a in alerts if a.get("status", {}).get("state") == "active"]
    if severity:
        alerts = [a for a in alerts if a.get("labels", {}).get("severity") == severity]
    if instance:
        alerts = [a for a in alerts if a.get("labels", {}).get("instance") == instance]

    if not alerts:
        cond = []
        if severity:
            cond.append(f"级别 {severity}")
        if instance:
            cond.append(f"实例 {instance}")
        scope = f"匹配（{('、'.join(cond))}）的" if cond else ""
        return f"当前没有{scope}正在触发的告警。"

    alerts.sort(
        key=lambda a: _SEVERITY_ORDER.get(a.get("labels", {}).get("severity"), 9)
    )

    lines: list[str] = []
    for a in alerts:
        labels = a.get("labels", {})
        name = labels.get("alertname", "（无名告警）")
        sev = labels.get("severity", "?")
        inst = labels.get("instance")
        summary = a.get("annotations", {}).get("summary", "")
        started = _fmt_time(a.get("startsAt"))
        where = f" @ {inst}" if inst else ""
        lines.append(f"- [{sev}] {name}{where}（起始 {started}）：{summary}")

    return f"当前正在触发的告警（{len(alerts)} 条，来自 Alertmanager）：\n" + "\n".join(
        lines
    )


def _fmt_time(ts: str | None) -> str:
    """Alertmanager 的 startsAt 是 RFC3339 UTC（末尾 Z），转成本地时区的「月-日 时:分」便于阅读。"""
    if not ts:
        return "未知"
    try:
        dt = datetime.fromisoformat(ts)  # Python 3.11+ 支持末尾 Z 和小数秒
    except ValueError:
        return ts
    return dt.astimezone().strftime("%m-%d %H:%M")
