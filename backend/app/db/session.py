from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.database_url)
# expire_on_commit=False：commit 后不用重新查一遍就能读属性。
# 异步模式下默认的“提交后过期、下次访问自动重新查询”这个隐式 IO 是同步阻塞的，
# 在 async 里直接触发会报 MissingGreenlet，所以这里必须关掉。
SessionLocal = async_sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as db:
        yield db
