"""事件相关的 Pydantic 模型（方向二·任务②）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

IncidentStatus = Literal["firing", "acknowledged", "resolved"]


class IncidentRead(BaseModel):
    """事件，给 GET /incidents 返回。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_key: str
    status: IncidentStatus
    alertname: str
    severity: str
    instance: str | None
    summary: str | None
    diagnosis: str | None
    triaged_at: datetime | None
    acked_by: str | None
    acked_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IncidentStatusCounts(BaseModel):
    """事件中心状态标签展示的全库数量，不受当前分页和状态筛选影响。"""

    all: int
    firing: int
    acknowledged: int
    resolved: int


class IncidentPageRead(BaseModel):
    """事件中心的数据库分页结果。"""

    items: list[IncidentRead]
    page: int
    page_size: int
    total: int
    total_pages: int
    status_counts: IncidentStatusCounts


class AckRequest(BaseModel):
    """认领事件的入参。acked_by 暂时是自由文本（还没接鉴权，方向三再接真身份）。"""

    acked_by: str
