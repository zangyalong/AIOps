from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server


async def list_all(db: AsyncSession) -> list[Server]:
    result = await db.scalars(select(Server).order_by(Server.created_at))
    return list(result)


async def get_by_id(db: AsyncSession, server_id: uuid.UUID) -> Server | None:
    return await db.get(Server, server_id)


async def get_by_name_or_ip(db: AsyncSession, identifier: str) -> Server | None:
    """按业务名或 IP 精确查服务器；名称优先，避免名称恰好与另一行 IP 相同时选错。"""
    result = await db.scalars(
        select(Server)
        .where(Server.name == identifier)
        .order_by(Server.created_at)
        .limit(1)
    )
    server = result.first()
    if server is not None:
        return server
    result = await db.scalars(
        select(Server)
        .where(Server.ip_address == identifier)
        .order_by(Server.created_at)
        .limit(1)
    )
    return result.first()


async def create(db: AsyncSession, server: Server) -> Server:
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return server


async def save(db: AsyncSession, server: Server) -> Server:
    """持久化已经在 session 里被修改过字段的 server（用于 update 场景）。"""
    await db.commit()
    await db.refresh(server)
    return server


async def delete(db: AsyncSession, server: Server) -> None:
    await db.delete(server)
    await db.commit()


async def try_lock_environment_deployment(
    db: AsyncSession, host: str, port: int
) -> bool:
    """同一 SSH 目标在当前事务内仅允许一份环境配置上传，事务结束自动释放。"""
    # 按实际 SSH 地址加锁，避免同一机器被登记为多个服务器后绕过串行限制。
    target = f"aiops:environment-deployment:{host.lower()}:{port}"
    key = int.from_bytes(
        hashlib.sha256(target.encode("utf-8")).digest()[:8], "big", signed=True
    )
    return bool(await db.scalar(select(func.pg_try_advisory_xact_lock(key))))
