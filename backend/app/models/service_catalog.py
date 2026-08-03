from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
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
    owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    repository_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    dashboard_url: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    metric_instance: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    log_service: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class ServiceDependency(Base, UUIDMixin, TimestampMixin):
    """依赖边：upstream（调用方）依赖 downstream（被调用方）。"""

    __tablename__ = "service_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "upstream_service_id",
            "downstream_service_id",
            name="uq_service_dependencies_edge",
        ),
        CheckConstraint(
            "upstream_service_id <> downstream_service_id",
            name="ck_service_dependencies_no_self_loop",
        ),
    )

    upstream_service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    downstream_service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dependency_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="service", server_default="service"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
