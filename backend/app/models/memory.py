from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Memory(Base, UUIDMixin, TimestampMixin):
    """从历史对话提炼出的跨会话长期记忆。

    ``scope_id`` 当前使用服务端可信 actor_id；以后接入登录后可以直接换成真实用户 ID。
    ``scope_type`` / ``server_id`` 决定它是跨服务器的全局记忆，还是仅属于某台已纳管
    服务器。服务器删除时，服务器记忆随外键级联删除，不会退化成全局记忆。
    ``memory_key`` 是同一事实的稳定键，供抽取器执行新增、纠正和遗忘，不把每次提及都
    堆成一条新记录。
    """

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "category IN ('preference', 'environment', 'decision', 'lesson')",
            name="ck_memories_category",
        ),
        CheckConstraint(
            "scope_type IN ('global', 'server')",
            name="ck_memories_scope_type",
        ),
        CheckConstraint(
            "(scope_type = 'global' AND server_id IS NULL) OR "
            "(scope_type = 'server' AND server_id IS NOT NULL)",
            name="ck_memories_scope_server",
        ),
        Index(
            "uq_memories_global_scope_key",
            "scope_id",
            "memory_key",
            unique=True,
            postgresql_where=text("scope_type = 'global'"),
        ),
        Index(
            "uq_memories_server_scope_key",
            "scope_id",
            "server_id",
            "memory_key",
            unique=True,
            postgresql_where=text("scope_type = 'server'"),
        ),
        Index(
            "ix_memories_scope_active_updated",
            "scope_id",
            "scope_type",
            "server_id",
            "is_active",
            "updated_at",
        ),
    )

    scope_id: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="global", server_default="global"
    )
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=True
    )
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
