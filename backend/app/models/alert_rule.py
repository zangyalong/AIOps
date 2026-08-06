from __future__ import annotations

from sqlalchemy import Boolean, String, Text, UniqueConstraint, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AlertRule(Base, UUIDMixin, TimestampMixin):
    """由 AIOMS 管理并同步到 Prometheus 的告警规则。"""

    __tablename__ = "alert_rules"
    __table_args__ = (UniqueConstraint("name", name="uq_alert_rules_name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    duration: Mapped[str] = mapped_column(String(30), nullable=False, default="0m")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
