"""新增持久化 chat_runs，并给已有 messages 表增加可空 run_id 外键。

``create_all`` 会创建全新的 chat_runs 表，但不会修改已经存在的 messages 表，所以本地已有
数据库需要运行本脚本。全部 DDL 都是幂等的，可重复执行；旧消息的 run_id 保持 NULL。

用法（从 backend/ 目录）：
    uv run python scripts/migrate_chat_runs.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.orm import configure_mappers

from app.db.session import engine
from app.models import chat_run, conversation, message  # noqa: F401
from app.models.chat_run import ChatRun


async def main() -> None:
    configure_mappers()
    async with engine.begin() as conn:
        await conn.run_sync(ChatRun.__table__.create, checkfirst=True)
        await conn.execute(
            text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS run_id UUID")
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'fk_messages_run_id_chat_runs'
                    ) THEN
                        ALTER TABLE messages
                        ADD CONSTRAINT fk_messages_run_id_chat_runs
                        FOREIGN KEY (run_id) REFERENCES chat_runs(id) ON DELETE SET NULL;
                    END IF;
                END
                $$;
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_messages_run_id_created_at "
                "ON messages (run_id, created_at)"
            )
        )
    print("done：chat_runs 表已就绪；messages.run_id 外键与索引已就绪")


if __name__ == "__main__":
    asyncio.run(main())
