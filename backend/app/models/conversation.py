from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Conversation(Base, UUIDMixin, TimestampMixin):
    """对话的元数据（标题、更新时间）。消息内容存在 messages 表，用 conversation_id 关联。"""

    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # 原始消息仍完整保存在 messages；这里只保存给模型使用的滚动摘要，以及摘要已经覆盖到
    # 哪条不可变消息。边界 UUID 不建反向外键，避免 conversations/messages 形成 DDL 环依赖。
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_summary_through_message_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True
    )
    memory_summary_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
