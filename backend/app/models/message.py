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
    # "user" | "assistant" | "tool"——忠实对应 LangChain 的 HumanMessage/AIMessage/ToolMessage
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # assistant 决定调工具时，这里存它的 tool_calls（[{name,args,id,type}, ...]）；否则 NULL
    tool_calls: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    # tool 消息回应的是哪个 tool_call（对应某条 assistant 的 tool_calls[i].id）；其它角色 NULL
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
