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
    scope_type: str = "global",
    server_id: uuid.UUID | None = None,
) -> Memory:
    timestamp = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return Memory(
        id=uuid.uuid4(),
        scope_id="local-user",
        scope_type=scope_type,
        server_id=server_id,
        memory_key=key,
        category=category,
        content=content,
        is_active=True,
        created_at=timestamp,
        updated_at=timestamp,
    )


class MemoryServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_resolve_memory_server_prefers_unique_explicit_target(self) -> None:
        default = SimpleNamespace(id=uuid.uuid4(), name="web-01", ip_address="10.0.0.1")
        explicit = SimpleNamespace(id=uuid.uuid4(), name="db-01", ip_address="10.0.0.2")

        resolved = memory_service.resolve_memory_server(
            "检查 db-01 的历史约定",
            [default, explicit],
            default,
        )
        ambiguous = memory_service.resolve_memory_server(
            "比较 web-01 和 db-01",
            [default, explicit],
            default,
        )

        self.assertIs(resolved, explicit)
        self.assertIsNone(ambiguous)

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

    async def test_search_combines_global_and_selected_server_memory(self) -> None:
        server_id = uuid.uuid4()
        global_memory = _memory(
            "preference.response.detail",
            "preference",
            "用户偏好简短回答。",
        )
        server_memory = _memory(
            "environment.web-01.role",
            "environment",
            "web-01 长期承载 nginx。",
            scope_type="server",
            server_id=server_id,
        )
        list_active = AsyncMock(side_effect=[[global_memory], [server_memory]])
        with patch.object(
            memory_service.memory_repository,
            "list_active",
            list_active,
        ):
            result = await memory_service.search_memories(
                AsyncMock(),
                scope_id="local-user",
                query="检查 nginx 配置",
                server_id=server_id,
                server_name="web-01",
            )

        self.assertEqual(
            {(item.scope_type, item.server_name) for item in result},
            {("global", None), ("server", "web-01")},
        )
        self.assertEqual(list_active.await_count, 2)

    async def test_server_operation_binds_backend_server_id(self) -> None:
        conversation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        server_id = uuid.uuid4()
        messages = [
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="user",
                content="web-01 长期承载 nginx，请记住。",
            ),
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="assistant",
                content="已确认这是该服务器的稳定角色。",
            ),
        ]
        llm = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    '{"operations":[{"action":"ADD","scope_type":"server",'
                    '"memory_key":"environment.web-01.role",'
                    '"category":"environment","content":"web-01 长期承载 nginx"}]}'
                )
            )
        )
        upsert = AsyncMock(
            return_value=_memory(
                "environment.web-01.role",
                "environment",
                "web-01 长期承载 nginx",
                scope_type="server",
                server_id=server_id,
            )
        )
        with (
            patch.object(
                memory_service.message_service,
                "list_run_messages",
                AsyncMock(return_value=messages),
            ),
            patch.object(
                memory_service.memory_repository,
                "list_active",
                AsyncMock(side_effect=[[], []]),
            ),
            patch.object(memory_service, "_memory_llm", return_value=llm),
            patch.object(memory_service.memory_repository, "upsert", upsert),
        ):
            result = await memory_service.consolidate_run_memories(
                AsyncMock(),
                scope_id="local-user",
                conversation_id=conversation_id,
                run_id=run_id,
                server_id=server_id,
                server_name="web-01",
            )

        self.assertEqual(result.added_or_updated, 1)
        self.assertEqual(upsert.await_args.kwargs["scope_type"], "server")
        self.assertEqual(upsert.await_args.kwargs["server_id"], server_id)

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
            '"content":"sk-'
            '1234567890abcdefghijklmnop"},'
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
