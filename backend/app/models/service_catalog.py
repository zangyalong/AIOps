from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Service(Base, UUIDMixin, TimestampMixin):
    """服务目录中的逻辑服务；同名服务可按环境分别登记。"""

    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("name", "environment", name="uq_services_name_environment"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class ServiceInstance(Base, UUIDMixin, TimestampMixin):
    """逻辑服务的一份部署实例，以及它与服务器和监控标签的映射。"""

    __tablename__ = "service_instances"
    __table_args__ = (
        UniqueConstraint(
            "service_id", "name", name="uq_service_instances_service_name"
        ),
        UniqueConstraint(
            "server_id",
            "runtime_type",
            "runtime_name",
            name="uq_service_instances_server_runtime",
        ),
        CheckConstraint(
            "runtime_type IS NULL OR runtime_type IN ('systemd', 'docker', 'process')",
            name="ck_service_instances_runtime_type",
        ),
        CheckConstraint(
            "control_enabled = false OR runtime_type IN ('systemd', 'docker')",
            name="ck_service_instances_control_runtime",
        ),
    )

    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    runtime_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    runtime_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )
    control_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    metric_instance: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    log_service: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class ServiceFileAccess(Base, UUIDMixin, TimestampMixin):
    """服务实例关联的纳管服务器文件授权；目标机仍保留独立 root 白名单。"""

    __tablename__ = "service_file_accesses"
    __table_args__ = (
        UniqueConstraint(
            "service_instance_id",
            "access_level",
            "path",
            name="uq_service_file_access_identity",
        ),
        CheckConstraint(
            "access_level IN ('read', 'write')",
            name="ck_service_file_access_level",
        ),
    )

    service_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("service_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    access_level: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
