from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RuntimeType = Literal["systemd", "docker", "process"]


def _required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class ServiceBase(BaseModel):
    name: str = Field(max_length=200)
    environment: str = Field(max_length=50)
    description: str | None = None

    _clean_required = field_validator("name", "environment")(_required_text)


class ServiceCreate(ServiceBase):
    pass


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    environment: str | None = Field(default=None, max_length=50)
    description: str | None = None

    @field_validator("name", "environment")
    @classmethod
    def clean_required_strings(cls, value: str | None) -> str | None:
        return _required_text(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        for field in ("name", "environment"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class ServiceRead(ServiceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ServiceInstanceBase(BaseModel):
    service_id: uuid.UUID
    server_id: uuid.UUID | None = None
    name: str = Field(max_length=200)
    runtime_type: RuntimeType | None = None
    runtime_name: str | None = Field(default=None, max_length=200)
    metric_instance: str | None = Field(default=None, max_length=255)
    log_service: str | None = Field(default=None, max_length=255)
    description: str | None = None

    _clean_name = field_validator("name")(_required_text)
    _clean_runtime_name = field_validator("runtime_name")(_optional_text)

    @model_validator(mode="after")
    def validate_runtime_identity(self) -> Self:
        if bool(self.runtime_type) != bool(self.runtime_name):
            raise ValueError("runtime_type 和 runtime_name 必须同时填写")
        if self.runtime_type and self.server_id is None:
            raise ValueError("填写运行时身份时必须绑定服务器")
        return self


class ServiceInstanceCreate(ServiceInstanceBase):
    pass


class ServiceInstanceUpdate(BaseModel):
    service_id: uuid.UUID | None = None
    server_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    runtime_type: RuntimeType | None = None
    runtime_name: str | None = Field(default=None, max_length=200)
    metric_instance: str | None = Field(default=None, max_length=255)
    log_service: str | None = Field(default=None, max_length=255)
    description: str | None = None

    @field_validator("name", "runtime_name")
    @classmethod
    def clean_optional_names(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        for field in ("service_id", "name"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class ServiceInstanceRead(ServiceInstanceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    control_enabled: bool
    created_at: datetime
    updated_at: datetime


class ServiceControlAuthorizationUpdate(BaseModel):
    enabled: bool


class ServiceFileAccessCreate(BaseModel):
    access_level: Literal["read", "write"]
    path: str = Field(min_length=1, max_length=4096)

    _clean_path = field_validator("path")(_required_text)

    @field_validator("path")
    @classmethod
    def validate_absolute_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("必须填写绝对路径")
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError("路径不能包含换行或空字符")
        return value


class ServiceFileAccessRead(ServiceFileAccessCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_instance_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
