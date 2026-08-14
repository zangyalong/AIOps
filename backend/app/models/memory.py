from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Memory(Base, UUIDMixin, TimestampMixin):
    """从历史对话提炼出的跨会话长期记忆。

    ``scope_id`` 当前使用服务端可信 actor_id；以后接入登录后可以直接换成真实用户 ID。
    ``memory_key`` 是同一事实的稳定键，供抽取器执行新增、纠正和遗忘，不把每次提及都
    堆成一条新记录。
    """

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "category IN ('preference', 'environment', 'decision', 'lesson')",
            name="ck_memories_category",
        ),
        UniqueConstraint("scope_id", "memory_key", name="uq_memories_scope_key"),
        Index(
            "ix_memories_scope_active_updated", "scope_id", "is_active", "updated_at"
        ),
    )

    scope_id: Mapped[str] = mapped_column(String(200), nullable=False)
    memory_key: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_runs.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
