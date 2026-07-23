"""事件相关的 Pydantic 模型（方向二·任务②）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.change_record import ChangeRecordRead


class IncidentRead(BaseModel):
    """事件，给 GET /incidents 返回。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_key: str
    status: str
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


class AckRequest(BaseModel):
    """认领事件的入参。acked_by 暂时是自由文本（还没接鉴权，方向三再接真身份）。"""

    acked_by: str


class IncidentChangesRead(BaseModel):
    """事件按实例映射到服务后，在事件发生前后查询到的变更上下文。"""

    model_config = ConfigDict(from_attributes=True)

    service_id: uuid.UUID | None
    service_name: str | None
    environment: str | None
    window_start: datetime
    window_end: datetime
    changes: list[ChangeRecordRead]
