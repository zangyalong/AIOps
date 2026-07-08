"""告警相关的 Pydantic 模型（方向二）。

AlertmanagerWebhook / AlertmanagerAlert 对应 Alertmanager 推来的 webhook 载荷（只声明我们用得到的
字段，其它忽略）；AlertRead 是 GET /alerts 的出参（从 ORM 读）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AlertmanagerAlert(BaseModel):
    """Alertmanager webhook 里 alerts[] 的单条。字段名用 alias 对上它的 camelCase JSON。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    status: str  # firing / resolved
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime | None = Field(default=None, alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    fingerprint: str


class AlertmanagerWebhook(BaseModel):
    """Alertmanager webhook 顶层载荷。我们只关心 alerts[]。"""

    model_config = ConfigDict(extra="ignore")

    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


class AlertRead(BaseModel):
    """落库后的告警，给 GET /alerts 返回。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fingerprint: str
    alertname: str
    severity: str
    status: str
    summary: str | None
    instance: str | None
    labels: dict | None
    starts_at: datetime | None
    ends_at: datetime | None
    diagnosis: str | None
    triaged_at: datetime | None
    created_at: datetime
    updated_at: datetime
