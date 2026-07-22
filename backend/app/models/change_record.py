from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ChangeRecord(Base, UUIDMixin, TimestampMixin):
    """服务变更事实：只记录发生了什么，不直接断言它就是故障根因。"""

    __tablename__ = "change_records"

    service_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    commit_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # deployment / rollback / scaling / configuration
    change_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
