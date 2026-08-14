"""升级现有开发库的 Memory 作用域与会话滚动摘要字段。

全新数据库由 ``Base.metadata.create_all()`` 直接创建完整结构；已经存在的表不会被
``create_all`` 修改，因此需要运行本脚本。DDL 全部幂等，可重复执行。

用法（从 backend/ 目录）：
    uv run python scripts/migrate_memory_scopes.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.orm import configure_mappers

from app.db.session import engine
from app.models import chat_run, conversation, memory, message, server  # noqa: F401
from app.models.base import Base


async def main() -> None:
    configure_mappers()
    async with engine.begin() as conn:
        # 允许从“尚未创建基础 memories 表”的更早开发库直接升级。
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "ALTER TABLE memories ADD COLUMN IF NOT EXISTS "
                "scope_type varchar(20) NOT NULL DEFAULT 'global'"
            )
        )
        await conn.execute(
            text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS server_id uuid")
        )
        await conn.execute(
            text("ALTER TABLE chat_runs ADD COLUMN IF NOT EXISTS memory_server_id uuid")
        )
        await conn.execute(
            text(
                "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS memory_summary text"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
                "memory_summary_through_message_id uuid"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
                "memory_summary_updated_at timestamptz"
            )
        )

        await conn.execute(
            text("ALTER TABLE memories DROP CONSTRAINT IF EXISTS uq_memories_scope_key")
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'ck_memories_scope_type'
                    ) THEN
                        ALTER TABLE memories ADD CONSTRAINT ck_memories_scope_type
                        CHECK (scope_type IN ('global', 'server'));
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'ck_memories_scope_server'
                    ) THEN
                        ALTER TABLE memories ADD CONSTRAINT ck_memories_scope_server
                        CHECK (
                            (scope_type = 'global' AND server_id IS NULL) OR
                            (scope_type = 'server' AND server_id IS NOT NULL)
                        );
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'fk_memories_server_id_servers'
                    ) THEN
                        ALTER TABLE memories
                        ADD CONSTRAINT fk_memories_server_id_servers
                        FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'fk_chat_runs_memory_server_id_servers'
                    ) THEN
                        ALTER TABLE chat_runs
                        ADD CONSTRAINT fk_chat_runs_memory_server_id_servers
                        FOREIGN KEY (memory_server_id) REFERENCES servers(id)
                        ON DELETE SET NULL;
                    END IF;
                END
                $$;
                """
            )
        )

        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_global_scope_key "
                "ON memories (scope_id, memory_key) WHERE scope_type = 'global'"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_server_scope_key "
                "ON memories (scope_id, server_id, memory_key) "
                "WHERE scope_type = 'server'"
            )
        )
        await conn.execute(
            text("DROP INDEX IF EXISTS ix_memories_scope_active_updated")
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_memories_scope_active_updated ON memories "
                "(scope_id, scope_type, server_id, is_active, updated_at)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chat_runs_memory_server_id "
                "ON chat_runs (memory_server_id)"
            )
        )

    print("done：两级 Memory 作用域、Chat Run 服务器绑定和会话摘要字段已就绪")


if __name__ == "__main__":
    asyncio.run(main())
