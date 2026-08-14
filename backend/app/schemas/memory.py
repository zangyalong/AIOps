from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryCategory = Literal["preference", "environment", "decision", "lesson"]


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    memory_key: str
    category: MemoryCategory
    content: str
    source_conversation_id: uuid.UUID | None
    source_run_id: uuid.UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class MemoryOperation(BaseModel):
    """LLM 只提交记忆意图；服务层仍会校验键、类别、长度和目标是否存在。"""

    action: Literal["ADD", "UPDATE", "DELETE", "NONE"]
    memory_key: str = Field(default="", max_length=120)
    category: MemoryCategory = "lesson"
    content: str = Field(default="", max_length=1000)


class MemoryPlan(BaseModel):
    operations: list[MemoryOperation] = Field(default_factory=list, max_length=5)


class MemoryClearResult(BaseModel):
    deactivated: int
