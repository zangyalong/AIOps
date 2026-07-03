from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.graphs.chat_graph import build_chat_graph
from app.api import chat, health, servers
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # checkpointer 接的是 LangGraph 自己的 Postgres 表（跟 SQLAlchemy/Alembic 管的业务表分开），
    # 用来持久化每个 thread_id 的对话状态，重启后端也不丢。
    async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_dsn) as checkpointer:
        await checkpointer.setup()
        app.state.chat_graph = build_chat_graph(checkpointer)
        yield


app = FastAPI(title="AIOMS Backend", lifespan=lifespan)

app.include_router(health.router)
app.include_router(servers.router)
app.include_router(chat.router)
