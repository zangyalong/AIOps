from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["info", "warning", "critical"]
RuntimeState = Literal["disabled", "inactive", "pending", "firing", "unknown"]
_ALERT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,199}")
_DURATION = re.compile(r"(?:0|[1-9][0-9]*)(?:ms|s|m|h|d|w|y)")
_LABEL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _required(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("不能为空")
    return cleaned


def _alert_name(value: str) -> str:
    if not _ALERT_NAME.fullmatch(value):
        raise ValueError("只能使用字母、数字和下划线，且不能以数字开头")
    return value


def _duration(value: str) -> str:
    cleaned = value.strip()
    if not _DURATION.fullmatch(cleaned):
        raise ValueError("持续时间格式应类似 0m、5m、1h")
    return cleaned


def _labels(value: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for name, label_value in value.items():
        if name == "severity":
            raise ValueError("severity 请使用严重级别字段配置")
        if not _LABEL_NAME.fullmatch(name):
            raise ValueError(f"标签名 {name} 不符合 Prometheus 格式")
        cleaned[name] = label_value.strip()
    return cleaned


class AlertRuleBase(BaseModel):
    name: str = Field(max_length=200)
    expression: str = Field(max_length=5000)
    duration: str = Field(default="0m", max_length=30)
    severity: Severity = "warning"
    summary: str = Field(max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    labels: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    _clean_required = field_validator("name", "expression", "summary")(_required)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _alert_name(value)

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, value: str) -> str:
        return _duration(value)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: dict[str, str]) -> dict[str, str]:
        return _labels(value)


class AlertRuleCreate(AlertRuleBase):
    pass


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    expression: str | None = Field(default=None, max_length=5000)
    duration: str | None = Field(default=None, max_length=30)
    severity: Severity | None = None
    summary: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    labels: dict[str, str] | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _alert_name(_required(value))

    @field_validator("expression", "summary")
    @classmethod
    def clean_required(cls, value: str | None) -> str | None:
        return _required(value) if value is not None else None

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, value: str | None) -> str | None:
        return _duration(value) if value is not None else None

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _labels(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        required = (
            "name",
            "expression",
            "duration",
            "severity",
            "summary",
            "labels",
            "enabled",
        )
        for field in required:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class AlertRuleRead(AlertRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    runtime_state: RuntimeState = "unknown"
    health: str = "unknown"
    last_error: str | None = None
    active_alerts: int = 0
    created_at: datetime
    updated_at: datetime


class AlertRuleOverviewRead(BaseModel):
    rules: list[AlertRuleRead]
    active_alerts_total: int = 0
    prometheus_message: str | None = None
    alertmanager_message: str | None = None


class AlertRuleMutationRead(BaseModel):
    message: str
    overview: AlertRuleOverviewRead
