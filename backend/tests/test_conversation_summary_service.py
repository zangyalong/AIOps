from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from app.models.conversation import Conversation
from app.models.message import Message
from app.services import conversation_summary_service


def _message(
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    *,
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
) -> Message:
    return Message(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )


class ConversationSummaryServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_compaction_summarizes_old_turn_and_keeps_recent_turns(self) -> None:
        conversation_id = uuid.uuid4()
        conversation = Conversation(
            id=conversation_id,
            title="长对话",
            memory_summary=None,
            memory_summary_through_message_id=None,
            memory_summary_updated_at=None,
        )
        first_turn = [
            _message(conversation_id, "user", "密码是 secret，调查旧服务。" * 8),
            _message(conversation_id, "assistant", "旧服务当时已完成调查。" * 8),
            _message(conversation_id, "tool", "不应进入摘要的超长日志" * 30),
        ]
        recent_turns = [
            _message(conversation_id, "user", "继续第二步。" * 8),
            _message(conversation_id, "assistant", "第二步已完成。" * 8),
            _message(conversation_id, "user", "继续第三步。" * 8),
            _message(conversation_id, "assistant", "第三步已完成。" * 8),
        ]
        llm = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    '{"summary":"用户在本会话中调查旧服务；历史上已完成第一步。"}'
                )
            )
        )
        save_summary = AsyncMock(return_value=conversation)

        with (
            patch.object(
                conversation_summary_service.settings,
                "deepseek_api_key",
                "test-key",
            ),
            patch.object(
                conversation_summary_service.settings,
                "memory_conversation_summary_trigger_chars",
                100,
            ),
            patch.object(
                conversation_summary_service.settings,
                "memory_conversation_recent_chars",
                100,
            ),
            patch.object(
                conversation_summary_service.settings,
                "memory_conversation_summary_input_chars",
                10_000,
            ),
            patch.object(
                conversation_summary_service.conversation_repository,
                "get_by_id",
                AsyncMock(return_value=conversation),
            ),
            patch.object(
                conversation_summary_service.message_repository,
                "list_by_conversation",
                AsyncMock(return_value=[*first_turn, *recent_turns]),
            ),
            patch.object(
                conversation_summary_service, "_summary_llm", return_value=llm
            ),
            patch.object(
                conversation_summary_service.conversation_repository,
                "save_memory_summary",
                save_summary,
            ),
        ):
            result = await conversation_summary_service.compact_conversation(
                AsyncMock(),
                conversation_id,
            )

        self.assertTrue(result.updated)
        self.assertEqual(result.summarized_messages, len(first_turn))
        self.assertEqual(
            save_summary.await_args.kwargs["through_message_id"],
            first_turn[-1].id,
        )
        prompt = llm.ainvoke.await_args.args[0][1].content
        self.assertNotIn("secret", prompt)
        self.assertNotIn("不应进入摘要的超长日志", prompt)

    async def test_compaction_skips_short_conversation(self) -> None:
        conversation_id = uuid.uuid4()
        conversation = Conversation(
            id=conversation_id,
            title="短对话",
            memory_summary=None,
            memory_summary_through_message_id=None,
            memory_summary_updated_at=None,
        )
        messages = [_message(conversation_id, "user", "你好")]
        with (
            patch.object(
                conversation_summary_service.settings,
                "deepseek_api_key",
                "test-key",
            ),
            patch.object(
                conversation_summary_service.conversation_repository,
                "get_by_id",
                AsyncMock(return_value=conversation),
            ),
            patch.object(
                conversation_summary_service.message_repository,
                "list_by_conversation",
                AsyncMock(return_value=messages),
            ),
            patch.object(
                conversation_summary_service,
                "_summary_llm",
                return_value=SimpleNamespace(ainvoke=AsyncMock()),
            ) as summary_llm,
        ):
            result = await conversation_summary_service.compact_conversation(
                AsyncMock(),
                conversation_id,
            )

        self.assertFalse(result.updated)
        summary_llm.return_value.ainvoke.assert_not_awaited()
