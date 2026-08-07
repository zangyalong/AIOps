from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
IMAGE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/@:-]*$"
NETWORK_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
USERNAME_PATTERN = r"^[A-Za-z0-9_.-]+$"


class TargetDeploymentConfig(BaseModel):
    """可长期保存的目标服务器部署参数，不包含任何密码。"""

    environment: str = Field(
        default="prod", min_length=1, max_length=50, pattern=NAME_PATTERN
    )

    enable_alloy: bool = True
    loki_push_url: str = Field(
        default="http://localhost:3100/loki/api/v1/push", max_length=500
    )
    allow_docker_socket: bool = False

    enable_node_exporter: bool = True
    node_exporter_image: str = Field(
        default="quay.io/prometheus/node-exporter:v1.11.1",
        min_length=1,
        max_length=300,
        pattern=IMAGE_PATTERN,
    )

    enable_cadvisor: bool = True
    cadvisor_image: str = Field(
        default="ghcr.io/google/cadvisor:0.57.0",
        min_length=1,
        max_length=300,
        pattern=IMAGE_PATTERN,
    )
    docker_root_dir: str = Field(
        default="/var/lib/docker", min_length=1, max_length=500
    )

    enable_mysql_exporter: bool = False
    mysql_exporter_image: str = Field(
        default="prom/mysqld-exporter:v0.19.0",
        min_length=1,
        max_length=300,
        pattern=IMAGE_PATTERN,
    )
    mysql_exporter_user: str = Field(
        default="aioms_exporter",
        min_length=1,
        max_length=100,
        pattern=USERNAME_PATTERN,
    )
    mysql_exporter_host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    mysql_exporter_port: int = Field(default=3306, ge=1, le=65535)
    mysql_exporter_network: str = Field(
        default="host", min_length=1, max_length=128, pattern=NETWORK_PATTERN
    )

    enable_postgres_exporter: bool = False
    postgres_exporter_image: str = Field(
        default="quay.io/prometheuscommunity/postgres-exporter:v0.19.1",
        min_length=1,
        max_length=300,
        pattern=IMAGE_PATTERN,
    )
    postgres_exporter_uri: str = Field(
        default="127.0.0.1:5432/postgres?sslmode=disable",
        min_length=1,
        max_length=500,
    )
    postgres_exporter_user: str = Field(
        default="aioms_exporter",
        min_length=1,
        max_length=100,
        pattern=USERNAME_PATTERN,
    )
    postgres_exporter_network: str = Field(
        default="host", min_length=1, max_length=128, pattern=NETWORK_PATTERN
    )

    enable_redis_exporter: bool = False
    redis_exporter_image: str = Field(
        default="quay.io/oliver006/redis_exporter:v1.88.0",
        min_length=1,
        max_length=300,
        pattern=IMAGE_PATTERN,
    )
    redis_exporter_address: str = Field(
        default="redis://127.0.0.1:6379", min_length=1, max_length=500
    )
    redis_exporter_user: str = Field(default="", max_length=100)
    redis_exporter_network: str = Field(
        default="host", min_length=1, max_length=128, pattern=NETWORK_PATTERN
    )

    start_exporters: bool = True

    @model_validator(mode="after")
    def validate_deployment_values(self) -> TargetDeploymentConfig:
        single_line_values = {
            "Loki 地址": self.loki_push_url,
            "Docker 数据目录": self.docker_root_dir,
            "MySQL 主机": self.mysql_exporter_host,
            "PostgreSQL URI": self.postgres_exporter_uri,
            "Redis 地址": self.redis_exporter_address,
            "Redis 用户名": self.redis_exporter_user,
        }
        for label, value in single_line_values.items():
            if "\n" in value or "\r" in value:
                raise ValueError(f"{label}不能包含换行")

        if self.enable_alloy and not (
            self.loki_push_url.startswith("http://")
            or self.loki_push_url.startswith("https://")
        ):
            raise ValueError("启用 Alloy 时 Loki 地址必须使用 http:// 或 https://")
        if not all(
            char.isalnum() or char in "_.:-" for char in self.mysql_exporter_host
        ):
            raise ValueError("MySQL exporter 主机格式不合法")
        if not self.docker_root_dir.startswith("/") or ".." in self.docker_root_dir:
            raise ValueError("Docker 数据目录必须是不包含 .. 的绝对路径")
        if self.enable_redis_exporter and not (
            self.redis_exporter_address.startswith("redis://")
            or self.redis_exporter_address.startswith("rediss://")
        ):
            raise ValueError("启用 Redis exporter 时地址必须使用 redis:// 或 rediss://")
        if self.redis_exporter_user and not all(
            char.isalnum() or char in "_.-" for char in self.redis_exporter_user
        ):
            raise ValueError("Redis exporter 用户名格式不合法")
        return self


class DeploymentBundleSecrets(BaseModel):
    """只用于本次打包，服务端不会把这些字段保存到数据库。"""

    mysql_password: SecretStr | None = None
    postgres_password: SecretStr | None = None
    redis_password: SecretStr | None = None


class DeploymentBundleRequest(BaseModel):
    secrets: DeploymentBundleSecrets = Field(default_factory=DeploymentBundleSecrets)


class ServerBase(BaseModel):
    name: str
    ip_address: str
    description: str | None = None
    # 三个都可选：不是每台 server 都要能真的 SSH 上去，见 app/models/server.py 的注释。
    ssh_port: int | None = None
    ssh_user: str | None = None
    ssh_key_filename: str | None = None
    deployment_config: TargetDeploymentConfig | None = None


class ServerCreate(ServerBase):
    pass


class ServerUpdate(BaseModel):
    name: str | None = None
    ip_address: str | None = None
    description: str | None = None
    ssh_port: int | None = None
    ssh_user: str | None = None
    ssh_key_filename: str | None = None
    deployment_config: TargetDeploymentConfig | None = None


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
