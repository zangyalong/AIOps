from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.repositories import server_repository
from app.schemas.server import ServerCreate, ServerUpdate


async def list_servers(db: AsyncSession) -> list[Server]:
    return await server_repository.list_all(db)


async def get_server(db: AsyncSession, server_id: uuid.UUID) -> Server | None:
    return await server_repository.get_by_id(db, server_id)


async def create_server(db: AsyncSession, data: ServerCreate) -> Server:
    server = Server(**data.model_dump())
    return await server_repository.create(db, server)


async def update_server(db: AsyncSession, server: Server, data: ServerUpdate) -> Server:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(server, field, value)
    return await server_repository.save(db, server)


async def delete_server(db: AsyncSession, server: Server) -> None:
    await server_repository.delete(db, server)
