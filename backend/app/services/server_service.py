from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.schemas.server import ServerCreate, ServerUpdate


async def list_servers(db: AsyncSession) -> list[Server]:
    result = await db.scalars(select(Server).order_by(Server.id))
    return list(result)


async def get_server(db: AsyncSession, server_id: int) -> Server | None:
    return await db.get(Server, server_id)


async def create_server(db: AsyncSession, data: ServerCreate) -> Server:
    server = Server(**data.model_dump())
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return server


async def update_server(db: AsyncSession, server: Server, data: ServerUpdate) -> Server:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(server, field, value)
    await db.commit()
    await db.refresh(server)
    return server


async def delete_server(db: AsyncSession, server: Server) -> None:
    await db.delete(server)
    await db.commit()
