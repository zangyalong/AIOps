from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.dependencies import DBSession
from app.schemas.memory import MemoryClearResult, MemoryOut
from app.services import memory_service

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    db: DBSession, include_inactive: bool = False
) -> list[MemoryOut]:
    memories = await memory_service.list_memories(
        db,
        settings.operation_actor_id,
        include_inactive=include_inactive,
    )
    return [MemoryOut.model_validate(item) for item in memories]


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
async def clear_memories(db: DBSession) -> MemoryClearResult:
    count = await memory_service.clear_memories(db, settings.operation_actor_id)
    return MemoryClearResult(deactivated=count)
