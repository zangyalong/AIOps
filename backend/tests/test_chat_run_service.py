from __future__ import annotations

import asyncio
import unittest
import uuid

from app.services.chat_run_service import ChatRunManager


class ChatRunManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_subscriber_does_not_cancel_background_run(self) -> None:
        """SSE 订阅者离开后，独立 producer 仍继续并产生 done。"""
        manager = ChatRunManager()
        release = asyncio.Event()

        async def producer():
            yield {"type": "token", "content": "前半段"}
            await release.wait()
            yield {"type": "done", "conversation_id": "conversation-1"}

        run = manager.start(uuid.uuid4(), producer)
        subscription = manager.subscribe(run.id)
        first = await anext(subscription)
        self.assertEqual(first["content"], "前半段")
        await subscription.aclose()

        release.set()
        await run.task

        self.assertEqual(run.status, "completed")
        self.assertEqual([event["type"] for event in run.events], ["token", "done"])

    async def test_reconnect_replays_buffered_events_in_order(self) -> None:
        """页面重新订阅时可以从头重建生成中的消息，并继续等到完成。"""
        manager = ChatRunManager()
        release = asyncio.Event()

        async def producer():
            yield {"type": "token", "content": "已经生成"}
            await release.wait()
            yield {"type": "token", "content": "，继续生成"}
            yield {"type": "done", "conversation_id": "conversation-2"}

        run = manager.start(uuid.uuid4(), producer)
        while not run.events:
            await asyncio.sleep(0)

        async def collect_replay() -> list[dict]:
            return [event async for event in manager.subscribe(run.id)]

        replay_task = asyncio.create_task(collect_replay())
        await asyncio.sleep(0)
        release.set()
        replay = await replay_task

        self.assertEqual([event["seq"] for event in replay], [1, 2, 3])
        self.assertEqual(
            [event["type"] for event in replay], ["token", "token", "done"]
        )

    async def test_approval_resume_keeps_same_run_and_continues_sequence(self) -> None:
        """审批前后属于同一 run，恢复订阅只需从旧 seq 后继续。"""
        manager = ChatRunManager()

        async def approval_producer():
            yield {
                "type": "approval_required",
                "thread_id": "thread-1",
                "conversation_id": "conversation-3",
                "actions": [],
            }

        run = manager.start(uuid.uuid4(), approval_producer)
        await run.task
        self.assertEqual(run.status, "waiting_approval")

        async def resumed_producer():
            yield {"type": "done", "conversation_id": "conversation-3"}

        await manager.resume(
            run.id,
            thread_id="thread-1",
            approved=True,
            producer=resumed_producer,
        )
        replay = [event async for event in manager.subscribe(run.id, after=1)]
        await run.task

        self.assertEqual(
            [event["type"] for event in replay], ["approval_resolved", "done"]
        )
        self.assertTrue(replay[0]["approved"])
        self.assertEqual(run.status, "completed")
