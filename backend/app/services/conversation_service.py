from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.repositories import conversation_repository


def make_title(first_message: str) -> str:
    text = first_message.strip().replace("\n", " ")
    return text[:30] + ("…" if len(text) > 30 else "")


async def create_conversation(db: AsyncSession, title: str) -> Conversation:
    conversation = Conversation(title=title)
    return await conversation_repository.create(db, conversation)


async def list_conversations(db: AsyncSession) -> list[Conversation]:
    return await conversation_repository.list_all(db)


async def get_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> Conversation | None:
    return await conversation_repository.get_by_id(db, conversation_id)


async def touch_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """有新消息时刷新 updated_at，让活跃对话排在列表前面。"""
    await conversation_repository.touch(db, conversation_id)


async def delete_conversation(db: AsyncSession, conversation: Conversation) -> None:
    await conversation_repository.delete(db, conversation)
