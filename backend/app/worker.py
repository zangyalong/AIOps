"""arq 后台任务 worker（方向二）。

跟后端 FastAPI 是**两个独立进程**：后端只负责往 Redis 队列里塞作业，这个 worker 从队列取作业来跑
（告警自动诊断、以后的定时值班报告等重活都在这里，不占后端请求线程）。

triage_alert 的做法（选型 A）：直接复用聊天用的那套 ReAct+Reflexion agent 图，把告警上下文投给它，
让它自己调 correlate_incident / query_metrics / search_logs 去调查、出诊断——同一个 agent，既能答用户
提问，也能自动诊断告警。

启动（从 backend/ 目录，跟后端分开的另一个终端）：
    uv run arq app.worker.WorkerSettings
"""

from __future__ import annotations

from arq.connections import RedisSettings
from langchain_core.messages import HumanMessage

from app.agents.graphs.chat_graph import build_chat_graph
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.alert_event import AlertEvent
from app.models.server import Server
from app.services import alert_event_service, server_service


async def triage_alert(ctx: dict, fingerprint: str) -> dict:
    """对一条新触发的告警自动诊断：载入告警 → 反查实体 → 跑 agent 调查 → 回填诊断。"""
    graph = ctx["chat_graph"]

    async with AsyncSessionLocal() as db:
        alert = await alert_event_service.get_alert(db, fingerprint)
        if alert is None or alert.status != "firing":
            print(f"[triage] {fingerprint} 不存在或已非 firing，跳过")
            return {"fingerprint": fingerprint, "skipped": True}
        # 反查实体：找 monitor_labels.instance 跟这条告警 instance 对上的服务器，好让 agent 用业务名关联
        servers = await server_service.list_servers(db)
        srv = next(
            (s for s in servers if (s.monitor_labels or {}).get("instance") == alert.instance),
            None,
        )
        prompt = _build_prompt(alert, srv)
        alertname = alert.alertname  # 提前取出来，出了 session 就别再碰 ORM 对象了

    print(f"[triage] 开始诊断 {alertname}（{fingerprint}）")
    try:
        state = await graph.ainvoke(
            {
                "messages": [HumanMessage(prompt)],
                "tool_iterations": 0,
                "reflections": [],
                "review_attempts": 0,
                "force_tools": False,
            }
        )
        final = state["messages"][-1]
        diagnosis = (getattr(final, "content", "") or "").strip() or "（agent 未产出诊断文本）"
    except Exception as exc:  # noqa: BLE001 —— 诊断失败也回填，方便排查，且不让 worker 崩
        diagnosis = f"自动诊断失败：{exc}"
        print(f"[triage] {fingerprint} 诊断出错：{exc}")

    async with AsyncSessionLocal() as db:
        await alert_event_service.save_diagnosis(db, fingerprint, diagnosis)
    print(f"[triage] {alertname}（{fingerprint}）诊断完成，{len(diagnosis)} 字")
    return {"fingerprint": fingerprint, "diagnosis_chars": len(diagnosis)}


def _build_prompt(alert: AlertEvent, srv: Server | None) -> str:
    who = f"服务器 {srv.name}" if srv else (f"实例 {alert.instance}" if alert.instance else "未知对象")
    lines = [
        "这是一个运维告警的自动诊断任务。下面这条告警刚刚触发，请你调用工具调查根因，"
        "给出简短诊断（3-5 句，说清可能原因 + 处理建议）：",
        f"- 告警名：{alert.alertname}",
        f"- 级别：{alert.severity}",
        f"- 对象：{who}" + (f"（监控实例 {alert.instance}）" if srv and alert.instance else ""),
        f"- 摘要：{alert.summary or '（无）'}",
    ]
    if srv:
        lines.append(
            f"提示：优先用 correlate_incident 关联 {srv.name} 此刻前后的指标+日志+告警来判断。"
        )
    elif alert.instance:
        lines.append(
            f"提示：用 query_metrics（instance={alert.instance}）、search_logs 等工具围绕该实例调查。"
        )
    return "\n".join(lines)


async def on_startup(ctx: dict) -> None:
    # 编译一次 agent 图，整个 worker 生命周期复用（每条告警都重新编译太浪费）。
    ctx["chat_graph"] = build_chat_graph()
    print("[worker] agent 图已就绪")


class WorkerSettings:
    """arq 约定：worker 从这个类读配置。functions 列出可执行任务，redis_settings 指 broker。"""

    functions = [triage_alert]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = on_startup
    job_timeout = 180  # agent 调查可能多轮工具调用 + 质检，给足超时
