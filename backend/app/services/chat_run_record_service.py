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
    memory_server_id: uuid.UUID | None = None,
) -> ChatRun:
    return await chat_run_repository.create(
        db,
        ChatRun(
            conversation_id=conversation_id,
            status="running",
            langgraph_thread_id=langgraph_thread_id,
            memory_server_id=memory_server_id,
        ),
    )


async def get_chat_run(db: AsyncSession, run_id: uuid.UUID) -> ChatRun | None:
    return await chat_run_repository.get_by_id(db, run_id)


async def list_chat_runs(db: AsyncSession, conversation_id: uuid.UUID) -> list[ChatRun]:
    return await chat_run_repository.list_by_conversation(db, conversation_id)


async def list_waiting_approval_runs(db: AsyncSession) -> list[ChatRun]:
    return await chat_run_repository.list_by_status(db, "waiting_approval")


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


async def fail_running_runs(db: AsyncSession) -> int:
    """执行中断的 Run 不自动重放；等待审批的 Run 由 PostgreSQL checkpoint 安全恢复。"""
    return await chat_run_repository.fail_running(
        db,
        error_message="后端进程已重启，本轮执行结果未知，请核验目标状态后重新发起请求。",
        finished_at=datetime.now(UTC),
    )
