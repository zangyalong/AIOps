"""为已有开发库补充 servers.monitor_labels 兼容字段。

全新数据库由 ``Base.metadata.create_all()`` 创建完整结构；已有 ``servers`` 表不会被
``create_all`` 修改，因此从早期版本升级时可运行本脚本。脚本只执行幂等 DDL，不写入任何
服务器地址或演示标签。

用法（从 backend/ 目录）：
    uv run python scripts/migrate_server_monitor_labels.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db.session import engine  # noqa: E402


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE servers ADD COLUMN IF NOT EXISTS monitor_labels JSONB")
        )
    print("done：servers.monitor_labels 兼容字段已就绪")


if __name__ == "__main__":
    asyncio.run(main())
