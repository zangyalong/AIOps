from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.graphs.chat_graph import build_chat_graph
from app.api import chat, conversations, health, servers
from app.db.session import engine
from app.models import conversation, message, server  # noqa: F401  # 注册模型到 Base.metadata
from app.models.base import Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 项目早期没有迁移工具，缺的表在启动时自动建出来；已存在的表不会被改动。
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="AIOMS Backend", lifespan=lifespan)
app.state.chat_graph = build_chat_graph()

app.include_router(health.router)
app.include_router(servers.router)
app.include_router(chat.router)
app.include_router(conversations.router)
