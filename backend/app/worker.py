"""arq 后台任务 worker（方向二）。

跟后端 FastAPI 是**两个独立进程**：后端只负责往 Redis 队列里塞作业，这个 worker 从队列取作业来跑
（告警自动诊断、以后的定时值班报告等重活都在这里，不占后端请求线程）。

triage_incident 复用聊天用的 Supervisor 图，由事件、指标、日志专家协作调查；worker 是无人值守场景，
会显式关闭 SSH 写工具，不能绕过聊天里的人工审批门。诊断回填到「事件（incident）」级，不是单条告警。

启动（从 backend/ 目录，跟后端分开的另一个终端）：
    uv run arq app.worker.WorkerSettings
"""

from __future__ import annotations

import uuid

from arq.connections import RedisSettings
from langchain_core.messages import HumanMessage

from app.agents.graphs.chat_graph import build_chat_graph
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.incident import RESOLVED, Incident
from app.models.server import Server
from app.services import incident_service, server_service


async def triage_incident(ctx: dict, incident_id: str) -> dict:
    """对一个新事件自动诊断：载入事件 → 反查实体 → 跑 agent 调查 → 诊断回填到事件。"""
    graph = ctx["chat_graph"]
    iid = uuid.UUID(incident_id)

    async with AsyncSessionLocal() as db:
        incident = await incident_service.get_incident(db, iid)
        if incident is None or incident.status == RESOLVED:
            print(f"[triage] 事件 {incident_id} 不存在或已 resolved，跳过")
            return {"incident_id": incident_id, "skipped": True}
        # 反查实体：找 monitor_labels.instance 跟事件 instance 对上的服务器，好让 agent 用业务名关联
        servers = await server_service.list_servers(db)
        srv = next(
            (
                s
                for s in servers
                if (s.monitor_labels or {}).get("instance") == incident.instance
            ),
            None,
        )
        prompt = _build_prompt(incident, srv)
        alertname = incident.alertname  # 提前取出来，出了 session 就别再碰 ORM 对象了

    print(f"[triage] 开始诊断事件 {alertname}（{incident_id}）")
    try:
        state = await graph.ainvoke(
            {
                "messages": [HumanMessage(prompt)],
                "work_messages": [],
                "reports": {},
                "specialist_instruction": "",
                "tool_iterations": 0,
                "supervisor_rounds": 0,
                "allow_write_tools": False,
                "approval": None,
            },
            # 同一事件的作业重试也要用新线程；否则 MemorySaver 会把上一次已完成状态叠进来。
            config={
                "configurable": {
                    "thread_id": f"triage-{incident_id}-{uuid.uuid4().hex}"
                }
            },
        )
        final = state["messages"][-1]
        diagnosis = (
            getattr(final, "content", "") or ""
        ).strip() or "（agent 未产出诊断文本）"
    except Exception as exc:  # noqa: BLE001 —— 诊断失败也回填，方便排查，且不让 worker 崩
        diagnosis = f"自动诊断失败：{exc}"
        print(f"[triage] 事件 {incident_id} 诊断出错：{exc}")

    async with AsyncSessionLocal() as db:
        await incident_service.save_diagnosis(db, iid, diagnosis)
    print(f"[triage] 事件 {alertname}（{incident_id}）诊断完成，{len(diagnosis)} 字")
    return {"incident_id": incident_id, "diagnosis_chars": len(diagnosis)}


def _build_prompt(incident: Incident, srv: Server | None) -> str:
    who = (
        f"服务器 {srv.name}"
        if srv
        else (f"实例 {incident.instance}" if incident.instance else "未知对象")
    )
    lines = [
        "这是一个运维事件的自动诊断任务。下面这个事件刚刚触发，请你调用工具调查根因，"
        "给出简短诊断（3-5 句，说清可能原因 + 处理建议）：",
        f"- 事件（告警名）：{incident.alertname}",
        f"- 级别：{incident.severity}",
        f"- 对象：{who}"
        + (f"（监控实例 {incident.instance}）" if srv and incident.instance else ""),
        f"- 摘要：{incident.summary or '（无）'}",
    ]
    if srv:
        lines.append(
            f"提示：优先用 correlate_incident 关联 {srv.name} 在 "
            f"{incident.created_at.isoformat()} 前后的指标、日志、告警和变更来判断。"
        )
    elif incident.instance:
        lines.append(
            f"提示：用 query_metrics（instance={incident.instance}）、search_logs 等工具围绕该实例调查。"
        )
    return "\n".join(lines)


async def on_startup(ctx: dict) -> None:
    # 编译一次 agent 图，整个 worker 生命周期复用（每个事件都重新编译太浪费）。
    ctx["chat_graph"] = build_chat_graph()
    print("[worker] Supervisor 图已就绪")


class WorkerSettings:
    """arq 约定：worker 从这个类读配置。functions 列出可执行任务，redis_settings 指 broker。"""

    functions = [triage_incident]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = on_startup
    job_timeout = 180  # agent 调查可能包含多轮工具调用，给足超时
