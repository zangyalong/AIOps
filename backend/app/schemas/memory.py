from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MemoryCategory = Literal["preference", "environment", "decision", "lesson"]
MemoryScopeType = Literal["global", "server"]


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    memory_key: str
    scope_type: MemoryScopeType
    server_id: uuid.UUID | None
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
    scope_type: MemoryScopeType = "global"
    memory_key: str = Field(default="", max_length=120)
    category: MemoryCategory = "lesson"
    content: str = Field(default="", max_length=1000)


class MemoryPlan(BaseModel):
    operations: list[MemoryOperation] = Field(default_factory=list, max_length=5)


class ConversationSummaryPlan(BaseModel):
    summary: str = Field(min_length=1, max_length=12000)


class MemoryCreate(BaseModel):
    """用户手动新增记忆；稳定键由后端生成，不把内部幂等协议暴露给页面。"""

    scope_type: MemoryScopeType = "global"
    server_id: uuid.UUID | None = None
    category: MemoryCategory
    content: str = Field(min_length=1, max_length=1000)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("记忆内容不能为空")
        return cleaned

    @model_validator(mode="after")
    def validate_scope(self) -> MemoryCreate:
        if self.scope_type == "global" and self.server_id is not None:
            raise ValueError("全局记忆不能绑定服务器")
        if self.scope_type == "server" and self.server_id is None:
            raise ValueError("服务器记忆必须选择一台已纳管服务器")
        if self.scope_type == "server" and self.category == "preference":
            raise ValueError("用户偏好应保存为全局记忆；服务器约定可使用环境或决策类别")
        return self


class MemoryClearResult(BaseModel):
    deactivated: int
