from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.server import Server
from app.schemas.server import ServerCreate, ServerRead, ServerUpdate
from app.services import server_service

router = APIRouter(prefix="/servers", tags=["servers"])


async def _get_server_or_404(db: AsyncSession, server_id: int) -> Server:
    server = await server_service.get_server(db, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")
    return server


@router.get("", response_model=list[ServerRead])
async def list_servers(db: AsyncSession = Depends(get_db)) -> list[Server]:
    return await server_service.list_servers(db)


@router.post("", response_model=ServerRead, status_code=201)
async def create_server(data: ServerCreate, db: AsyncSession = Depends(get_db)) -> Server:
    return await server_service.create_server(db, data)


@router.get("/{server_id}", response_model=ServerRead)
async def get_server(server_id: int, db: AsyncSession = Depends(get_db)) -> Server:
    return await _get_server_or_404(db, server_id)


@router.patch("/{server_id}", response_model=ServerRead)
async def update_server(
    server_id: int, data: ServerUpdate, db: AsyncSession = Depends(get_db)
) -> Server:
    server = await _get_server_or_404(db, server_id)
    return await server_service.update_server(db, server, data)


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: int, db: AsyncSession = Depends(get_db)) -> None:
    server = await _get_server_or_404(db, server_id)
    await server_service.delete_server(db, server)
