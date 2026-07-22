from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ChangeType = Literal["deployment", "rollback", "scaling", "configuration"]


def _strip_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("必须包含时区")
    return value


class ChangeRecordBase(BaseModel):
    service_name: str = Field(max_length=200)
    environment: str = Field(max_length=50)
    version: str | None = Field(default=None, max_length=100)
    commit_hash: str | None = Field(default=None, max_length=128)
    change_type: ChangeType
    occurred_at: datetime
    description: str | None = None

    _clean_required = field_validator("service_name", "environment")(_strip_required)
    _validate_occurred_at = field_validator("occurred_at")(_require_timezone)


class ChangeRecordCreate(ChangeRecordBase):
    pass


class ChangeRecordUpdate(BaseModel):
    service_name: str | None = Field(default=None, max_length=200)
    environment: str | None = Field(default=None, max_length=50)
    version: str | None = Field(default=None, max_length=100)
    commit_hash: str | None = Field(default=None, max_length=128)
    change_type: ChangeType | None = None
    occurred_at: datetime | None = None
    description: str | None = None

    @field_validator("service_name", "environment")
    @classmethod
    def clean_required_strings(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        required = ("service_name", "environment", "change_type", "occurred_at")
        for field in required:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class ChangeRecordRead(ChangeRecordBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
