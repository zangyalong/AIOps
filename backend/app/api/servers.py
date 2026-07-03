from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import DBSession
from app.models.server import Server
from app.schemas.server import ServerCreate, ServerRead, ServerUpdate
from app.services import server_service

router = APIRouter(prefix="/servers", tags=["servers"])


async def _get_server_or_404(db: AsyncSession, server_id: uuid.UUID) -> Server:
    server = await server_service.get_server(db, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")
    return server


@router.get("", response_model=list[ServerRead])
async def list_servers(db: DBSession) -> list[Server]:
    return await server_service.list_servers(db)


@router.post("", response_model=ServerRead, status_code=201)
async def create_server(data: ServerCreate, db: DBSession) -> Server:
    return await server_service.create_server(db, data)


@router.get("/{server_id}", response_model=ServerRead)
async def get_server(server_id: uuid.UUID, db: DBSession) -> Server:
    return await _get_server_or_404(db, server_id)


@router.patch("/{server_id}", response_model=ServerRead)
async def update_server(
    server_id: uuid.UUID, data: ServerUpdate, db: DBSession
) -> Server:
    server = await _get_server_or_404(db, server_id)
    return await server_service.update_server(db, server, data)


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: uuid.UUID, db: DBSession) -> None:
    server = await _get_server_or_404(db, server_id)
    await server_service.delete_server(db, server)
