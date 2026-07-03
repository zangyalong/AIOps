from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Conversation(Base, UUIDMixin, TimestampMixin):
    """对话的元数据（标题、更新时间）。消息内容存在 messages 表，用 conversation_id 关联。"""

    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
