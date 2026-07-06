from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChatRequest(BaseModel):
    message: str
    # 同一个 conversation_id 的多轮对话，每次请求都从 messages 表把历史加载出来
    # 喂给图；第一次不传，后端建一条新 conversation，SSE 流结束时用 "done" 事件带回去。
    conversation_id: uuid.UUID | None = None


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    role: str
    content: str
    # assistant 消息"最终答案之前"的有序过程（旁白文字段 + 工具），前端刷新后据此原样重建；
    # 每个元素是 {"type":"text","content":...} 或 {"type":"tool","tool":...}。否则为 None。
    steps: list[dict] | None = None


class ConversationDetail(BaseModel):
    conversation: ConversationSummary
    messages: list[ChatMessageOut]
