from __future__ import annotations

from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from app.agents.graphs.chat_graph import build_chat_graph
from app.api import (
    alerts,
    changes,
    chat,
    conversations,
    health,
    incidents,
    servers,
    services,
)
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import (  # noqa: F401  # 注册模型到 Base.metadata
    alert_event,
    change_record,
    chat_run,
    conversation,
    incident,
    message,
    server,
    service_catalog,
)
from app.models.base import Base
from app.services import chat_run_record_service
from app.services.chat_run_service import ChatRunManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 项目早期没有迁移工具，缺的表在启动时自动建出来；已存在的表不会被改动。
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        stale_runs = await chat_run_record_service.fail_incomplete_runs(db)
    if stale_runs:
        print(
            f"[startup] 已把 {stale_runs} 个失去内存 checkpoint 的 Chat Run 标记为失败"
        )

    # arq 连接池：webhook 用它往队列塞诊断作业。Redis 没起时也别让后端崩——置 None，
    # webhook 会跳过入队（只是暂时没有自动诊断，落库照常）。
    try:
        app.state.arq_pool = await create_pool(
            RedisSettings.from_dsn(settings.redis_url)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] 连接 Redis 失败，自动诊断入队将跳过：{exc}")
        app.state.arq_pool = None

    app.state.chat_run_manager = ChatRunManager()

    yield

    await app.state.chat_run_manager.shutdown()
    if getattr(app.state, "arq_pool", None) is not None:
        await app.state.arq_pool.close()


app = FastAPI(title="AIOMS Backend", lifespan=lifespan)
app.state.chat_graph = build_chat_graph()

app.include_router(health.router)
app.include_router(servers.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(alerts.router)
app.include_router(incidents.router)
app.include_router(changes.router)
app.include_router(services.router)
