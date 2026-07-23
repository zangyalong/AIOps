from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DependencyType = Literal["service", "database", "cache", "queue", "external"]


def _required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


class ServiceBase(BaseModel):
    name: str = Field(max_length=200)
    environment: str = Field(max_length=50)
    owner: str | None = Field(default=None, max_length=100)
    description: str | None = None
    repository_url: str | None = None
    dashboard_url: str | None = None

    _clean_required = field_validator("name", "environment")(_required_text)


class ServiceCreate(ServiceBase):
    pass


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    environment: str | None = Field(default=None, max_length=50)
    owner: str | None = Field(default=None, max_length=100)
    description: str | None = None
    repository_url: str | None = None
    dashboard_url: str | None = None

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
    metric_instance: str | None = Field(default=None, max_length=255)
    log_service: str | None = Field(default=None, max_length=255)
    description: str | None = None

    _clean_name = field_validator("name")(_required_text)


class ServiceInstanceCreate(ServiceInstanceBase):
    pass


class ServiceInstanceUpdate(BaseModel):
    service_id: uuid.UUID | None = None
    server_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    metric_instance: str | None = Field(default=None, max_length=255)
    log_service: str | None = Field(default=None, max_length=255)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return _required_text(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        for field in ("service_id", "name"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class ServiceInstanceRead(ServiceInstanceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ServiceDependencyBase(BaseModel):
    upstream_service_id: uuid.UUID
    downstream_service_id: uuid.UUID
    dependency_type: DependencyType = "service"
    description: str | None = None


class ServiceDependencyCreate(ServiceDependencyBase):
    pass


class ServiceDependencyUpdate(BaseModel):
    upstream_service_id: uuid.UUID | None = None
    downstream_service_id: uuid.UUID | None = None
    dependency_type: DependencyType | None = None
    description: str | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        required = (
            "upstream_service_id",
            "downstream_service_id",
            "dependency_type",
        )
        for field in required:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class ServiceDependencyRead(ServiceDependencyBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ServiceTopologyRead(BaseModel):
    focus_service_id: uuid.UUID
    nodes: list[ServiceRead]
    edges: list[ServiceDependencyRead]
