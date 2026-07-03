from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    # 同一个 thread_id 的多轮对话共享 LangGraph checkpointer 里持久化的历史；
    # 第一次不传，后端生成一个新的并在响应里带回去，前端后续请求带着它。
    thread_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    thread_id: str
