from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AlertEvent(Base, UUIDMixin, TimestampMixin):
    """运维告警事件。现在数据来自 scripts/seed_alerts.py 手动模拟生成，还没接入真实监控系统。"""

    __tablename__ = "alert_events"

    server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=True
    )
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # "P1" / "P2" / "P3"
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # "open" / "resolved"
