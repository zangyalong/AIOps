from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.models.chat_run import ChatRun
from app.services import chat_run_record_service


class ChatRunRecordServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_chat_run_schema_does_not_duplicate_message_body(self) -> None:
        self.assertEqual(
            set(ChatRun.__table__.columns.keys()),
            {
                "id",
                "conversation_id",
                "memory_server_id",
                "status",
                "langgraph_thread_id",
                "error_message",
                "started_at",
                "finished_at",
            },
        )
        self.assertNotIn("content", ChatRun.__table__.columns)

    async def test_completed_run_gets_finished_timestamp(self) -> None:
        chat_run = ChatRun(
            id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            status="running",
            langgraph_thread_id=uuid.uuid4().hex,
        )
        save = AsyncMock(side_effect=lambda _db, item: item)

        with patch.object(chat_run_record_service.chat_run_repository, "save", save):
            result = await chat_run_record_service.set_status(
                AsyncMock(),
                chat_run,
                "completed",
            )

        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(result.finished_at)
        self.assertIsNone(result.error_message)

    async def test_terminal_run_cannot_return_to_running(self) -> None:
        chat_run = ChatRun(
            id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            status="failed",
            langgraph_thread_id=uuid.uuid4().hex,
            error_message="模型超时",
        )

        with self.assertRaisesRegex(ValueError, "不能从 failed 变为 running"):
            await chat_run_record_service.set_status(
                AsyncMock(),
                chat_run,
                "running",
            )
