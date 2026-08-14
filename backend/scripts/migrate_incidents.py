"""方向二·任务②的表结构迁移：引入 incidents 事件表，alert_events 挂 incident_id、诊断字段搬走。

create_all 只建缺的表、不改已有表，所以：incidents 是新表可自动建（这里也显式建一下），但
alert_events 加列/删列要手动 ALTER。都是幂等的，可重复跑。诊断从 alert_events 搬到 incidents，
alert_events 的 diagnosis/triaged_at 直接删（里面是可丢的 mock 诊断）。

用法（从 backend/ 目录）：
    uv run python scripts/migrate_incidents.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import configure_mappers  # noqa: E402

from app.db.session import engine  # noqa: E402

# 导入相关 model：注册到 metadata + 让 FK 目标（incidents/servers）可解析
from app.models import alert_event, incident, server  # noqa: E402, F401
from app.models.incident import Incident  # noqa: E402


async def main() -> None:
    configure_mappers()  # 解析 Mapped 注解里的列类型（单独建表要先做，否则 FK 列是 NullType）
    async with engine.begin() as conn:
        # 1. 建 incidents 表（若无）
        await conn.run_sync(Incident.__table__.create, checkfirst=True)
        # 2. alert_events 加 incident_id 外键列（指向刚建的 incidents）
        await conn.execute(
            text(
                "ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS incident_id UUID "
                "REFERENCES incidents(id) ON DELETE SET NULL"
            )
        )
        # 3. 诊断字段搬到 incidents 了，从 alert_events 删掉
        await conn.execute(
            text("ALTER TABLE alert_events DROP COLUMN IF EXISTS diagnosis")
        )
        await conn.execute(
            text("ALTER TABLE alert_events DROP COLUMN IF EXISTS triaged_at")
        )
    print(
        "done：incidents 表已建；alert_events 加 incident_id、删 diagnosis/triaged_at"
    )


if __name__ == "__main__":
    asyncio.run(main())
