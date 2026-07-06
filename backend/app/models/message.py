from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class Message(Base, UUIDMixin):
    """一行一条消息，user/assistant 都存这张表，按 conversation_id + created_at 排序还原历史。

    消息是不可变的（发出去就不会再改），所以只继承 UUIDMixin，不用 TimestampMixin——
    没有 updated_at 这种对消息来说没有意义的字段。
    """

    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)  # assistant 存最终答案
    # assistant 消息"最终答案之前"的有序过程（档位 B 完整回放）：一串
    # {"type":"text","content":旁白} / {"type":"tool","tool":工具名}，供刷新后原样重建。
    # user 消息、以及没有中间过程的直接回答都是 NULL。
    steps: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
