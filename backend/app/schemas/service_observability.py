from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RuntimeStatus = Literal["running", "stopped", "error", "unknown"]
LogLevel = Literal["error", "warn", "info", "unknown"]


class ServiceInstanceStatusRead(BaseModel):
    instance_id: uuid.UUID
    status: RuntimeStatus
    label: str
    detail: str
    ports: list[str] = Field(default_factory=list)
    checked_at: datetime


class ServiceMetricRead(BaseModel):
    key: str
    label: str
    value: float
    unit: str


class ServiceMetricPointRead(BaseModel):
    timestamp: datetime
    value: float


class ServiceMetricSeriesRead(BaseModel):
    key: str
    label: str
    unit: str
    points: list[ServiceMetricPointRead]


class ServiceLogRead(BaseModel):
    timestamp: datetime
    level: LogLevel
    line: str


class ServiceObservabilityRead(BaseModel):
    instance_id: uuid.UUID
    range_minutes: int
    metric_instance: str | None
    log_service: str | None
    metrics: list[ServiceMetricRead]
    logs: list[ServiceLogRead]
    metric_message: str | None = None
    log_message: str | None = None


class ServerObservabilityRead(BaseModel):
    server_id: uuid.UUID
    range_minutes: int
    metric_instance: str | None
    metrics: list[ServiceMetricRead]
    series: list[ServiceMetricSeriesRead]
    grafana_url: str | None = None
    metric_message: str | None = None
    series_message: str | None = None
    checked_at: datetime
