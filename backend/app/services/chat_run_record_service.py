from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_run import ChatRun
from app.repositories import chat_run_repository

ChatRunStatus = Literal["running", "waiting_approval", "completed", "failed"]
_TERMINAL_STATUSES = frozenset({"completed", "failed"})
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "running": frozenset({"running", "waiting_approval", "completed", "failed"}),
    "waiting_approval": frozenset({"waiting_approval", "running", "failed"}),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed"}),
}


async def create_chat_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    langgraph_thread_id: str,
) -> ChatRun:
    return await chat_run_repository.create(
        db,
        ChatRun(
            conversation_id=conversation_id,
            status="running",
            langgraph_thread_id=langgraph_thread_id,
        ),
    )


async def get_chat_run(db: AsyncSession, run_id: uuid.UUID) -> ChatRun | None:
    return await chat_run_repository.get_by_id(db, run_id)


async def list_chat_runs(db: AsyncSession, conversation_id: uuid.UUID) -> list[ChatRun]:
    return await chat_run_repository.list_by_conversation(db, conversation_id)


async def set_status(
    db: AsyncSession,
    chat_run: ChatRun,
    status: ChatRunStatus,
    *,
    error_message: str | None = None,
) -> ChatRun:
    """执行精简状态机；终态不可重新激活，重复写同一状态保持幂等。"""
    allowed = _ALLOWED_TRANSITIONS.get(chat_run.status, frozenset())
    if status not in allowed:
        raise ValueError(f"chat run 状态不能从 {chat_run.status} 变为 {status}")

    chat_run.status = status
    chat_run.error_message = error_message if status == "failed" else None
    if status in _TERMINAL_STATUSES:
        chat_run.finished_at = chat_run.finished_at or datetime.now(UTC)
    else:
        chat_run.finished_at = None
    return await chat_run_repository.save(db, chat_run)


async def fail_incomplete_runs(db: AsyncSession) -> int:
    """方案 B 没有持久化 checkpoint；进程重启后旧挂起 Run 必须明确标为失败。"""
    return await chat_run_repository.fail_incomplete(
        db,
        error_message="后端进程已重启，内存中的执行状态或审批断点已丢失，请重新发起请求。",
        finished_at=datetime.now(UTC),
    )
