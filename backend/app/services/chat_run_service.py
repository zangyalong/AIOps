"""与浏览器连接解耦的聊天运行管理器。

LangGraph 在独立 asyncio.Task 中执行；SSE 连接只是事件订阅者。浏览器切页、刷新或关闭导致
订阅断开时，运行任务仍会继续并最终由 chat_service 把完整消息落库。运行中的事件保存在内存中，
同一后端进程内重新打开对话可以从头重放当前进度。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Literal

from app.services.chat_service import ChatStreamEvent

ChatRunStatus = Literal["running", "waiting_approval", "completed", "failed"]
EventProducer = Callable[[], AsyncIterator[ChatStreamEvent]]
_CLOSED_STATUSES = frozenset({"waiting_approval", "completed", "failed"})


@dataclass
class ChatRun:
    id: str
    conversation_id: uuid.UUID
    status: ChatRunStatus = "running"
    events: list[ChatStreamEvent] = field(default_factory=list)
    approval_thread_id: str | None = None
    task: asyncio.Task[None] | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    @property
    def last_sequence(self) -> int:
        return len(self.events)

    async def publish(self, event: ChatStreamEvent) -> None:
        """给事件编号并唤醒全部订阅者；编号用于审批后只续传新事件。"""
        async with self.condition:
            stored: ChatStreamEvent = {**event, "seq": self.last_sequence + 1}
            self.events.append(stored)
            event_type = event.get("type")
            if event_type == "approval_required":
                self.status = "waiting_approval"
                self.approval_thread_id = event.get("thread_id")
            elif event_type == "done":
                self.status = "completed"
            elif event_type == "error":
                self.status = "failed"
            self.condition.notify_all()


class ChatRunManager:
    """管理当前进程中的聊天运行与可重放 SSE 事件。"""

    def __init__(self, *, max_finished_runs: int = 100) -> None:
        self._runs: dict[str, ChatRun] = {}
        self._active_by_conversation: dict[uuid.UUID, str] = {}
        self._max_finished_runs = max_finished_runs

    def start(self, conversation_id: uuid.UUID, producer: EventProducer) -> ChatRun:
        active = self.get_active(conversation_id)
        if active is not None:
            raise ValueError("该对话已有回复正在生成，请等待完成后再发送新消息")

        self._trim_finished_runs()
        run = ChatRun(id=uuid.uuid4().hex, conversation_id=conversation_id)
        self._runs[run.id] = run
        self._active_by_conversation[conversation_id] = run.id
        self._launch(run, producer)
        return run

    def get(self, run_id: str) -> ChatRun | None:
        return self._runs.get(run_id)

    def get_active(self, conversation_id: uuid.UUID) -> ChatRun | None:
        run_id = self._active_by_conversation.get(conversation_id)
        if run_id is None:
            return None
        run = self._runs.get(run_id)
        if run is None or run.status in {"completed", "failed"}:
            self._active_by_conversation.pop(conversation_id, None)
            return None
        return run

    async def resume(
        self,
        run_id: str,
        *,
        thread_id: str,
        approved: bool,
        producer: EventProducer,
    ) -> ChatRun:
        run = self.get(run_id)
        if run is None:
            raise LookupError("chat run not found")
        if run.status != "waiting_approval":
            raise ValueError("该对话当前没有等待审批的操作")
        if run.approval_thread_id != thread_id:
            raise ValueError("审批线程与当前挂起操作不匹配")

        run.status = "running"
        await run.publish(
            {
                "type": "approval_resolved",
                "thread_id": thread_id,
                "approved": approved,
            }
        )
        self._launch(run, producer)
        return run

    async def subscribe(
        self, run_id: str, *, after: int = 0
    ) -> AsyncIterator[ChatStreamEvent]:
        run = self.get(run_id)
        if run is None:
            raise LookupError("chat run not found")

        cursor = max(after, 0)
        while True:
            async with run.condition:
                await run.condition.wait_for(
                    lambda: run.last_sequence > cursor or run.status in _CLOSED_STATUSES
                )
                batch = [
                    event for event in run.events if int(event.get("seq", 0)) > cursor
                ]
                status = run.status

            for event in batch:
                cursor = int(event.get("seq", cursor))
                yield event

            if status in _CLOSED_STATUSES and cursor >= run.last_sequence:
                return

    async def shutdown(self) -> None:
        """应用退出时收拢后台任务；浏览器断开不会调用这里。"""
        tasks = [run.task for run in self._runs.values() if run.task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _launch(self, run: ChatRun, producer: EventProducer) -> None:
        run.task = asyncio.create_task(
            self._produce(run, producer),
            name=f"chat-run-{run.id}",
        )

    async def _produce(self, run: ChatRun, producer: EventProducer) -> None:
        try:
            async for event in producer():
                await run.publish(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后台任务必须把失败转成可重放事件
            await run.publish({"type": "error", "message": str(exc)})
        finally:
            if run.status == "running":
                await run.publish(
                    {"type": "error", "message": "对话任务意外结束，未生成完整回答"}
                )
            if run.status in {"completed", "failed"}:
                current = self._active_by_conversation.get(run.conversation_id)
                if current == run.id:
                    self._active_by_conversation.pop(run.conversation_id, None)

    def _trim_finished_runs(self) -> None:
        finished_ids = [
            run_id
            for run_id, run in self._runs.items()
            if run.status in {"completed", "failed"}
        ]
        for run_id in finished_ids[: -self._max_finished_runs]:
            self._runs.pop(run_id, None)
