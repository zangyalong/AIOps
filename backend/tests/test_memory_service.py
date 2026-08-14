from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from app.models.memory import Memory
from app.models.message import Message
from app.services import memory_service


def _memory(
    key: str,
    category: str,
    content: str,
    *,
    minutes_ago: int = 0,
) -> Memory:
    timestamp = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return Memory(
        id=uuid.uuid4(),
        scope_id="local-user",
        memory_key=key,
        category=category,
        content=content,
        is_active=True,
        created_at=timestamp,
        updated_at=timestamp,
    )


class MemoryServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_keeps_global_preferences_but_filters_unrelated_environment(
        self,
    ) -> None:
        candidates = [
            _memory(
                "preference.response.detail",
                "preference",
                "用户偏好简短回答。",
                minutes_ago=5,
            ),
            _memory(
                "environment.web-01.role",
                "environment",
                "web-01 是线上 Web 服务器。",
                minutes_ago=1,
            ),
        ]
        with patch.object(
            memory_service.memory_repository,
            "list_active",
            AsyncMock(return_value=candidates),
        ):
            result = await memory_service.search_memories(
                AsyncMock(),
                scope_id="local-user",
                query="如何设计告警规则？",
            )

        self.assertEqual([item.category for item in result], ["preference"])

    async def test_consolidation_rejects_secret_and_ephemeral_state(self) -> None:
        conversation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        messages = [
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="user",
                content="记住 API key 是 sk-secret，web-01 当前 CPU 使用率 98%。",
            ),
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="assistant",
                content="不会把敏感凭据当成长记忆。",
            ),
        ]
        llm = AsyncMock()
        llm.ainvoke.return_value = AIMessage(
            '{"operations":['
            '{"action":"ADD","memory_key":"environment.secret",'
            '"category":"environment","content":"API key 是 sk-secret"},'
            '{"action":"ADD","memory_key":"environment.web-01.cpu",'
            '"category":"lesson","content":"web-01 当前 CPU 使用率 98%"},'
            '{"action":"ADD","memory_key":"environment.unlabeled-secret",'
            '"category":"environment",'
            '"content":"sk-1234567890abcdefghijklmnop"},'
            '{"action":"ADD","memory_key":"preference.response.detail",'
            '"category":"preference","content":"用户偏好简短回答"}]}'
        )
        upsert = AsyncMock(
            return_value=_memory(
                "preference.response.detail",
                "preference",
                "用户偏好简短回答",
            )
        )
        db = AsyncMock()

        with (
            patch.object(
                memory_service.message_service,
                "list_run_messages",
                AsyncMock(return_value=messages),
            ),
            patch.object(
                memory_service.memory_repository,
                "list_active",
                AsyncMock(return_value=[]),
            ),
            patch.object(memory_service, "_memory_llm", return_value=llm),
            patch.object(memory_service.memory_repository, "upsert", upsert),
        ):
            result = await memory_service.consolidate_run_memories(
                db,
                scope_id="local-user",
                conversation_id=conversation_id,
                run_id=run_id,
            )

        self.assertEqual(result.added_or_updated, 1)
        self.assertEqual(result.skipped, 3)
        upsert.assert_awaited_once()
        self.assertEqual(
            upsert.await_args.kwargs["memory_key"],
            "preference.response.detail",
        )
        db.commit.assert_awaited_once()

    async def test_update_requires_existing_key(self) -> None:
        conversation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        messages = [
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="user",
                content="以后回答简短一些。",
            ),
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="assistant",
                content="好的。",
            ),
        ]
        llm = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    '{"operations":[{"action":"UPDATE",'
                    '"memory_key":"preference.response.detail",'
                    '"category":"preference","content":"用户偏好简短回答"}]}'
                )
            )
        )
        upsert = AsyncMock()
        with (
            patch.object(
                memory_service.message_service,
                "list_run_messages",
                AsyncMock(return_value=messages),
            ),
            patch.object(
                memory_service.memory_repository,
                "list_active",
                AsyncMock(return_value=[]),
            ),
            patch.object(memory_service, "_memory_llm", return_value=llm),
            patch.object(memory_service.memory_repository, "upsert", upsert),
        ):
            result = await memory_service.consolidate_run_memories(
                AsyncMock(),
                scope_id="local-user",
                conversation_id=conversation_id,
                run_id=run_id,
            )

        self.assertEqual(result.skipped, 1)
        upsert.assert_not_awaited()

    async def test_add_cannot_overwrite_existing_key(self) -> None:
        conversation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        existing = _memory(
            "preference.response.detail",
            "preference",
            "用户偏好简短回答",
        )
        messages = [
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="user",
                content="继续保持之前的回答方式。",
            ),
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="assistant",
                content="好的。",
            ),
        ]
        llm = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    '{"operations":[{"action":"ADD",'
                    '"memory_key":"preference.response.detail",'
                    '"category":"preference","content":"覆盖旧偏好"}]}'
                )
            )
        )
        upsert = AsyncMock()
        with (
            patch.object(
                memory_service.message_service,
                "list_run_messages",
                AsyncMock(return_value=messages),
            ),
            patch.object(
                memory_service.memory_repository,
                "list_active",
                AsyncMock(return_value=[existing]),
            ),
            patch.object(memory_service, "_memory_llm", return_value=llm),
            patch.object(memory_service.memory_repository, "upsert", upsert),
        ):
            result = await memory_service.consolidate_run_memories(
                AsyncMock(),
                scope_id="local-user",
                conversation_id=conversation_id,
                run_id=run_id,
            )

        self.assertEqual(result.skipped, 1)
        upsert.assert_not_awaited()
