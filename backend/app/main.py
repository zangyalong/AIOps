from __future__ import annotations

from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from app.agents.checkpoint import (
    create_checkpoint_pool,
    create_postgres_checkpointer,
)
from app.agents.graphs.chat_graph import build_chat_graph, graph_run_config
from app.api import (
    alert_rules,
    alerts,
    chat,
    conversations,
    health,
    incidents,
    memories,
    operations,
    servers,
    services,
    skills,
)
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import (  # noqa: F401  # 注册模型到 Base.metadata
    alert_event,
    alert_occurrence,
    alert_rule,
    chat_run,
    conversation,
    incident,
    memory,
    message,
    operation_record,
    server,
    service_catalog,
)
from app.models.base import Base
from app.services import (
    alert_occurrence_service,
    alert_rule_service,
    chat_run_record_service,
    chat_service,
    operation_record_service,
)
from app.services.chat_run_service import ChatRunManager


async def _restore_waiting_approvals(app: FastAPI) -> tuple[int, int]:
    """只恢复尚未执行的审批断点；checkpoint 与审计记录必须互相吻合。"""
    restored = 0
    failed = 0
    async with AsyncSessionLocal() as db:
        waiting_runs = await chat_run_record_service.list_waiting_approval_runs(db)
        for run in waiting_runs:
            try:
                snapshot = await app.state.chat_graph.aget_state(
                    graph_run_config(run.langgraph_thread_id)
                )
            except Exception as exc:  # noqa: BLE001 - 单条坏快照必须失败关闭但不阻断启动
                await chat_run_record_service.set_status(
                    db,
                    run,
                    "failed",
                    error_message=(
                        "审批断点无法安全读取，系统拒绝自动恢复；请重新发起请求。"
                    ),
                )
                print(f"[startup] 读取审批断点 {run.id} 失败：{exc}")
                failed += 1
                continue
            pending_records = await operation_record_service.list_operation_records(
                db,
                chat_run_id=run.id,
                status="pending_approval",
            )
            checkpoint_actions = (
                snapshot.interrupts[0].value.get("actions", [])
                if snapshot.interrupts
                and isinstance(snapshot.interrupts[0].value, dict)
                else []
            )
            record_bindings = {
                (str(record.id), record.tool_call_id) for record in pending_records
            }
            action_bindings = {
                (
                    str(action.get("operation_id") or ""),
                    str(action.get("tool_call_id") or ""),
                )
                for action in checkpoint_actions
                if isinstance(action, dict)
            }
            if (
                not checkpoint_actions
                or not record_bindings
                or action_bindings != record_bindings
            ):
                await chat_run_record_service.set_status(
                    db,
                    run,
                    "failed",
                    error_message=(
                        "审批断点或操作审计记录不完整，系统拒绝自动恢复；请重新发起请求。"
                    ),
                )
                failed += 1
                continue
            app.state.chat_run_manager.restore_waiting_approval(
                run_id=run.id,
                conversation_id=run.conversation_id,
                thread_id=run.langgraph_thread_id,
                actions=checkpoint_actions,
                on_completed=chat_service.consolidate_chat_run_memories,
            )
            restored += 1
    return restored, failed


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 项目早期没有迁移工具，缺的表在启动时自动建出来；已存在的表不会被改动。
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    checkpoint_pool = create_checkpoint_pool()
    await checkpoint_pool.open(wait=True)
    checkpointer = create_postgres_checkpointer(checkpoint_pool)
    await checkpointer.setup()
    app.state.chat_graph = build_chat_graph(checkpointer=checkpointer)
    app.state.chat_run_manager = ChatRunManager()
    # alert_rules 表是唯一配置源；YAML 只是 Prometheus 必需的运行时生成产物。
    # Prometheus 暂时不可用时文件仍已生成，后端继续启动，用户可稍后在页面重新应用。
    async with AsyncSessionLocal() as db:
        try:
            await alert_rule_service.sync_rules(db)
        except alert_rule_service.AlertRuleApplyError as exc:
            print(f"[startup] 预警规则文件已生成，但同步 Prometheus 失败：{exc}")
    async with AsyncSessionLocal() as db:
        backfilled_occurrences = (
            await alert_occurrence_service.backfill_existing_incidents(db)
        )
    if backfilled_occurrences:
        print(f"[startup] 已为 {backfilled_occurrences} 条历史事件补齐原始告警快照")
    async with AsyncSessionLocal() as db:
        incomplete_operations = (
            await operation_record_service.fail_incomplete_operations(db)
        )
    if incomplete_operations:
        print(f"[startup] 已把 {incomplete_operations} 条中断的操作记录标记为失败")
    async with AsyncSessionLocal() as db:
        interrupted_runs = await chat_run_record_service.fail_running_runs(db)
    if interrupted_runs:
        print(f"[startup] 已把 {interrupted_runs} 个执行中断的 Chat Run 标记为失败")
    restored_approvals, failed_approvals = await _restore_waiting_approvals(app)
    if restored_approvals:
        print(f"[startup] 已恢复 {restored_approvals} 个等待人工审批的 Chat Run")
    if failed_approvals:
        print(f"[startup] 已拒绝恢复 {failed_approvals} 个记录不完整的审批断点")

    # arq 连接池：webhook 用它往队列塞诊断作业。Redis 没起时也别让后端崩——置 None，
    # webhook 会跳过入队（只是暂时没有自动诊断，落库照常）。
    try:
        app.state.arq_pool = await create_pool(
            RedisSettings.from_dsn(settings.redis_url)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] 连接 Redis 失败，自动诊断入队将跳过：{exc}")
        app.state.arq_pool = None

    yield

    await app.state.chat_run_manager.shutdown()
    if getattr(app.state, "arq_pool", None) is not None:
        await app.state.arq_pool.close()
    await checkpoint_pool.close()


app = FastAPI(title="AIOps Backend", lifespan=lifespan)

app.include_router(health.router)
app.include_router(servers.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(alerts.router)
app.include_router(alert_rules.router)
app.include_router(incidents.router)
app.include_router(services.router)
app.include_router(skills.router)
app.include_router(operations.router)
app.include_router(memories.router)
