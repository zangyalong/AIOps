from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AlertOccurrence(Base, UUIDMixin, TimestampMixin):
    """一次原始告警从 firing 到 resolved 的历史快照。

    ``alert_events`` 保留每个 fingerprint 的当前状态；本表按 incident 保存每次触发周期，
    使同一 fingerprint 恢复后再次触发时不会丢失旧事件的原始告警关联。
    """

    __tablename__ = "alert_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "fingerprint",
            "incident_id",
            name="uq_alert_occurrences_fingerprint_incident",
        ),
    )

    alert_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alert_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    alertname: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    instance: Mapped[str | None] = mapped_column(String(255), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
