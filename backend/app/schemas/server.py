from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ServerBase(BaseModel):
    name: str
    ip_address: str
    description: str | None = None
    # 三个都可选：不是每台 server 都要能真的 SSH 上去，见 app/models/server.py 的注释。
    ssh_port: int | None = None
    ssh_user: str | None = None
    ssh_key_filename: str | None = None


class ServerCreate(ServerBase):
    pass


class ServerUpdate(BaseModel):
    name: str | None = None
    ip_address: str | None = None
    description: str | None = None
    ssh_port: int | None = None
    ssh_user: str | None = None
    ssh_key_filename: str | None = None


class ServerRead(ServerBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # 服务端管理的只读字段：最近一次测试连接的结果，创建时不能自己设。
    status: str
    created_at: datetime
    updated_at: datetime


class ConnectionTestResult(BaseModel):
    # active=连接成功；unreachable=尝试连接但失败；unconfigured=没配置 SSH 信息，压根没尝试
    status: Literal["active", "unreachable", "unconfigured"]
    detail: str
