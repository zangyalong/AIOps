from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class ChatRun(Base, UUIDMixin):
    """一轮用户请求的持久执行状态；消息正文仍只存在 messages 表。"""

    __tablename__ = "chat_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'waiting_approval', 'completed', 'failed')",
            name="ck_chat_runs_status",
        ),
        Index("ix_chat_runs_conversation_status", "conversation_id", "status"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default="running"
    )
    langgraph_thread_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
