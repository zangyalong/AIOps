from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.core.config import settings
from app.dependencies import DBSession
from app.schemas.memory import (
    MemoryClearResult,
    MemoryCreate,
    MemoryOut,
    MemoryScopeType,
)
from app.services import memory_service

router = APIRouter(prefix="/memories", tags=["memories"])


def _validate_scope(
    scope_type: MemoryScopeType,
    server_id: uuid.UUID | None,
) -> None:
    if scope_type == "global" and server_id is not None:
        raise HTTPException(status_code=422, detail="全局记忆不能绑定服务器")
    if scope_type == "server" and server_id is None:
        raise HTTPException(status_code=422, detail="服务器记忆必须指定 server_id")


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    db: DBSession,
    include_inactive: bool = False,
    scope_type: MemoryScopeType = Query(default="global"),
    server_id: uuid.UUID | None = None,
) -> list[MemoryOut]:
    _validate_scope(scope_type, server_id)
    memories = await memory_service.list_memories(
        db,
        settings.operation_actor_id,
        scope_type=scope_type,
        server_id=server_id,
        include_inactive=include_inactive,
    )
    return [MemoryOut.model_validate(item) for item in memories]


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(data: MemoryCreate, db: DBSession) -> MemoryOut:
    try:
        memory = await memory_service.create_memory(
            db,
            settings.operation_actor_id,
            data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MemoryOut.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_memory(memory_id: uuid.UUID, db: DBSession) -> None:
    forgotten = await memory_service.forget_memory(
        db,
        settings.operation_actor_id,
        memory_id,
    )
    if not forgotten:
        raise HTTPException(status_code=404, detail="memory not found")


@router.delete("", response_model=MemoryClearResult)
async def clear_memories(
    db: DBSession,
    scope_type: MemoryScopeType = Query(default="global"),
    server_id: uuid.UUID | None = None,
) -> MemoryClearResult:
    _validate_scope(scope_type, server_id)
    count = await memory_service.clear_memories(
        db,
        settings.operation_actor_id,
        scope_type=scope_type,
        server_id=server_id,
    )
    return MemoryClearResult(deactivated=count)
