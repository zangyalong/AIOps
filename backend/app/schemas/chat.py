from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    message: str
    # 同一个 conversation_id 的多轮对话，每次请求都从 messages 表把历史加载出来
    # 喂给图；第一次不传，后端创建 conversation，并立即返回独立 Chat Run 的标识。
    conversation_id: uuid.UUID | None = None
    # 前端选择的默认服务器只传 UUID；后端会重新查服务器目录，并把可信名称/IP 拼进本轮
    # system prompt。它不写进用户消息，也不授予任何额外操作权限。
    default_server_id: uuid.UUID | None = None


class OperationApprovalDecision(BaseModel):
    """用户对 approval_required 中某一项冻结操作作出的独立决议。"""

    tool_call_id: str = Field(min_length=1)
    operation_id: uuid.UUID
    approved: bool
    second_confirmation: bool = False
    reason: str | None = None


class ResumeApprovalRequest(BaseModel):
    """恢复挂起对话：每个待批操作都必须带回独立且可核对的裁决。"""

    run_id: str
    conversation_id: uuid.UUID
    thread_id: str
    decisions: list[OperationApprovalDecision] = Field(min_length=1)


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
    tool_status: Literal["completed", "failed"] | None = None
    approval_args: dict | None = None


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
