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
    role: str  # "user" | "assistant" | "tool"
    content: str
    # assistant 调工具时带上它的 tool_calls（[{name,args,id,type}]）；tool 消息带 tool_call_id。
    # 前端刷新后据此原样重建"旁白 → 工具行 → 答案"。
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


class ConversationDetail(BaseModel):
    conversation: ConversationSummary
    messages: list[ChatMessageOut]
