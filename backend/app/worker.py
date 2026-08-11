"""arq 后台任务 worker（方向二）。

跟后端 FastAPI 是**两个独立进程**：后端只负责往 Redis 队列里塞作业，这个 worker 从队列取作业来跑
（告警自动诊断、以后的定时值班报告等重活都在这里，不占后端请求线程）。

triage_incident 复用聊天用的统一 Ops Agent 图；worker 是无人值守场景，会在模型绑定和工具执行
两层显式关闭写工具，不能绕过聊天里的人工审批门。诊断回填到「事件（incident）」级，不是单条告警。

启动（从 backend/ 目录，跟后端分开的另一个终端）：
    uv run arq app.worker.WorkerSettings
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from arq.connections import RedisSettings
from langchain_core.messages import HumanMessage

from app.agents.graphs.chat_graph import build_chat_graph
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.incident import RESOLVED, Incident
from app.models.server import Server
from app.models.service_catalog import Service
from app.services import incident_service, server_service, service_catalog_service


async def triage_incident(ctx: dict, incident_id: str) -> dict:
    """对一个新事件自动诊断：载入事件 → 反查实体 → 跑 agent 调查 → 诊断回填到事件。"""
    graph = ctx["chat_graph"]
    iid = uuid.UUID(incident_id)

    async with AsyncSessionLocal() as db:
        incident = await incident_service.get_incident(db, iid)
        if incident is None or incident.status == RESOLVED:
            print(f"[triage] 事件 {incident_id} 不存在或已 resolved，跳过")
            return {"incident_id": incident_id, "skipped": True}
        resolved_service = await service_catalog_service.resolve_by_metric_instance(
            db, incident.instance
        )
        service = resolved_service.service if resolved_service else None
        srv = None
        if resolved_service and resolved_service.instance.server_id:
            srv = await server_service.get_server(
                db, resolved_service.instance.server_id
            )
        if srv is None:
            # 兼容服务目录落地前的旧 monitor_labels 映射。
            servers = await server_service.list_servers(db)
            srv = next(
                (
                    s
                    for s in servers
                    if (s.monitor_labels or {}).get("instance") == incident.instance
                ),
                None,
            )
        prompt = _build_prompt(incident, srv, service)
        alertname = incident.alertname  # 提前取出来，出了 session 就别再碰 ORM 对象了

    print(f"[triage] 开始诊断事件 {alertname}（{incident_id}）")
    try:
        state = await graph.ainvoke(
            {
                "messages": [HumanMessage(prompt)],
                "tool_iterations": 0,
                "allow_write_tools": False,
                "approval": None,
                "operation_execution_id": str(uuid.uuid4()),
                "operation_chat_run_id": None,
                "operation_context": {
                    "actor_id": "arq-worker",
                    "actor_role": "system",
                    "run_mode": "unattended",
                    "environment": "production",
                    "auto_approve_non_production_medium": False,
                },
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


def _build_prompt(
    incident: Incident, srv: Server | None, service: Service | None
) -> str:
    who = (
        f"服务器 {srv.name}"
        if srv
        else (f"实例 {incident.instance}" if incident.instance else "未知对象")
    )
    lines = [
        (
            "这是一个运维事件的自动诊断任务。下面这个事件刚刚触发，请你调用工具调查根因，"
            "给出简短诊断（3-5 句，说清可能原因 + 处理建议）："
        ),
        f"- 事件（告警名）：{incident.alertname}",
        f"- 级别：{incident.severity}",
        f"- 对象：{who}"
        + (f"（监控实例 {incident.instance}）" if srv and incident.instance else ""),
        f"- 摘要：{incident.summary or '（无）'}",
    ]
    if service:
        lines.append(f"- 关联服务：{service.name}/{service.environment}")
    if srv:
        service_args = (
            f"service={service.name}，environment={service.environment}"
            if service
            else "无"
        )
        lines.append(
            f"提示：优先调用 investigate(area=incident, target={srv.name})，关联 "
            f"{incident.created_at.isoformat()} 前后的指标、日志和告警来判断"
            f"；补充参数：{service_args}。"
        )
    elif incident.instance:
        lines.append(
            f"提示：使用 investigate 的 metrics/logs/alerts 领域围绕实例 "
            f"{incident.instance} 调查。"
        )
    return "\n".join(lines)


async def on_startup(ctx: dict) -> None:
    # 编译一次 agent 图，整个 worker 生命周期复用（每个事件都重新编译太浪费）。
    ctx["chat_graph"] = build_chat_graph()
    print("[worker] 统一 Ops Agent 图已就绪")


class WorkerSettings:
    """arq 约定：worker 从这个类读配置。functions 列出可执行任务，redis_settings 指 broker。"""

    functions: ClassVar = [triage_incident]
    redis_settings: ClassVar = RedisSettings.from_dsn(settings.redis_url)
    on_startup: ClassVar = on_startup
    job_timeout: ClassVar[int] = 180  # agent 调查可能包含多轮工具调用，给足超时
