from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings


def checkpoint_connection_string() -> str:
    """把 SQLAlchemy URL 收敛成 psycopg 原生连接串。"""
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def create_checkpoint_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=checkpoint_connection_string(),
        min_size=1,
        max_size=settings.database_pool_size,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        name="aiops-langgraph-checkpoints",
    )


def create_postgres_checkpointer(
    pool: AsyncConnectionPool,
) -> AsyncPostgresSaver:
    # 只允许 LangGraph 内建安全类型（其中包含 LangChain 消息和 Interrupt）；数据库即使被
    # 篡改，也不能借 checkpoint 反序列化任意 Python 类。
    serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
    return AsyncPostgresSaver(pool, serde=serializer)
