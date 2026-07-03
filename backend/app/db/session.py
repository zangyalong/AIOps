"""数据库连接 —— 异步 SQLAlchemy 2.0 + psycopg（异步模式）。

约定：
- 业务路由通过 ``DBSession``（见 ``app.dependencies``）注入 ``AsyncSession``，
  不要在路由函数里再写一遍 ``Depends(get_db)``。
- 请求正常结束（依赖退出）自动 commit；抛异常自动 rollback。
- 需要跟请求生命周期脱钩的场景（比如流式响应结束后才落库、LangChain 工具里单独查库），
  直接 ``async with AsyncSessionLocal() as session:`` 开一个独立 session，
  不要指望复用某个请求传进来的 session。
- 显式事务请在 service 层使用 ``async with session.begin():``。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# 创建数据库引擎：一个存放数据库连接的连接池，支持异步操作。全局只 new 一次，
# 后续都通过 AsyncSessionLocal() 从这个池子借连接。
engine = create_async_engine(
    settings.database_url,  # postgresql+psycopg://... 连哪个库、用哪个驱动
    echo=settings.database_echo,  # 是否把每条 SQL 打到日志（调试用，默认 False）
    pool_size=settings.database_pool_size,  # 连接池常驻连接数
    max_overflow=settings.database_max_overflow,  # 高峰再额外借的连接数上限
    pool_pre_ping=True,  # 取连接前先 ping 一下，避开已经断开的“死连接”
)

# 创建数据库会话工厂。expire_on_commit=False：commit 后不用重新查一遍就能读属性——
# 异步模式下默认的“提交后过期、下次访问自动重新查询”这个隐式 IO 是同步阻塞的，
# 在 async 里直接触发会报 MissingGreenlet，所以这里必须关掉。
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：注入数据库会话，请求正常结束自动 commit，抛异常自动 rollback。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
