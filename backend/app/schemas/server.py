from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ServerBase(BaseModel):
    name: str
    ip_address: str
    description: str | None = None


class ServerCreate(ServerBase):
    pass


class ServerUpdate(BaseModel):
    name: str | None = None
    ip_address: str | None = None
    description: str | None = None


class ServerRead(ServerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
