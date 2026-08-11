"""让 operation_records 同时审计交互对话和无人值守调查。

新记录使用独立 execution_id 绑定一次图执行；交互操作额外关联 chat_run_id，worker 调查的
chat_run_id 为 NULL。脚本会把旧记录的 execution_id 回填为原 chat_run_id，全部 DDL 幂等。

用法（从 backend/ 目录）：
    uv run python scripts/migrate_operation_records.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.orm import configure_mappers

from app.db.session import engine
from app.models import chat_run, operation_record  # noqa: F401
from app.models.operation_record import OperationRecord


async def main() -> None:
    configure_mappers()
    async with engine.begin() as conn:
        await conn.run_sync(OperationRecord.__table__.create, checkfirst=True)
        await conn.execute(
            text(
                "ALTER TABLE operation_records "
                "ADD COLUMN IF NOT EXISTS execution_id UUID"
            )
        )
        await conn.execute(
            text(
                "UPDATE operation_records SET execution_id = chat_run_id "
                "WHERE execution_id IS NULL"
            )
        )
        await conn.execute(
            text("ALTER TABLE operation_records ALTER COLUMN execution_id SET NOT NULL")
        )
        await conn.execute(
            text("ALTER TABLE operation_records ALTER COLUMN chat_run_id DROP NOT NULL")
        )
        await conn.execute(
            text(
                "ALTER TABLE operation_records "
                "DROP CONSTRAINT IF EXISTS uq_operation_records_run_tool_call"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE operation_records DROP CONSTRAINT IF EXISTS "
                "uq_operation_records_execution_tool_call"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE operation_records ADD CONSTRAINT "
                "uq_operation_records_execution_tool_call "
                "UNIQUE (execution_id, tool_call_id)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE operation_records "
                "ADD COLUMN IF NOT EXISTS exit_code INTEGER"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE operation_records "
                "ADD COLUMN IF NOT EXISTS timed_out BOOLEAN"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE operation_records "
                "DROP CONSTRAINT IF EXISTS ck_operation_records_effect"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE operation_records ADD CONSTRAINT "
                "ck_operation_records_effect CHECK (effect IN ('read', 'write'))"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE operation_records "
                "DROP CONSTRAINT IF EXISTS ck_operation_records_risk_level"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE operation_records ADD CONSTRAINT "
                "ck_operation_records_risk_level "
                "CHECK (risk_level IN ('low', 'medium', 'high', 'critical'))"
            )
        )
    print("done：operation_records 已支持独立 execution_id 和无人值守调查审计")


if __name__ == "__main__":
    asyncio.run(main())
