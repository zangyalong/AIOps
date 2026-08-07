from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ChatRequest(BaseModel):
    message: str
    # 同一个 conversation_id 的多轮对话，每次请求都从 messages 表把历史加载出来
    # 喂给图；第一次不传，后端创建 conversation，并立即返回独立 Chat Run 的标识。
    conversation_id: uuid.UUID | None = None


class ResumeApprovalRequest(BaseModel):
    """恢复一次挂在写操作审批门上的对话：带回 approval_required 里发出的 thread_id + 用户裁决。"""

    run_id: str
    conversation_id: uuid.UUID
    thread_id: str
    approved: bool
    # high / critical 操作必须经过审批卡片 + 详情弹窗两步确认。后端仍校验这个标记，
    # 防止前端漏做弹窗时直接恢复高风险写工具。
    second_confirmation: bool = False
    # 拒绝时可选带一句理由，会回喂给 agent 让它据此向用户说明
    reason: str | None = None


class ChatRunOut(BaseModel):
    run_id: str
    conversation_id: uuid.UUID
    status: Literal["running", "waiting_approval", "completed", "failed"]
    title: str | None = None


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    role: str  # "user" | "assistant" | "tool"
    content: str
    run_id: uuid.UUID | None = None
    # assistant 调工具时带上它的 tool_calls（[{name,args,id,type}]）；tool 消息带 tool_call_id。
    # 前端刷新后据此原样重建"旁白 → 工具行 → 答案"。
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


class ChatRunHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: Literal["running", "waiting_approval", "completed", "failed"]
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class ConversationDetail(BaseModel):
    conversation: ConversationSummary
    runs: list[ChatRunHistoryOut]
    messages: list[ChatMessageOut]
