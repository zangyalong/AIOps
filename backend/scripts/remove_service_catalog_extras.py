"""删除服务目录中不再使用的元数据列和服务依赖表。

``create_all`` 不会修改已有表，所以代码模型删除字段后，现有开发数据库还需要执行一次
DDL。脚本是幂等的，可以重复运行；但 ``service_dependencies`` 中的旧数据会被永久删除。

用法（从 backend/ 目录）：
    uv run python scripts/remove_service_catalog_extras.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db.session import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS service_dependencies"))
        await conn.execute(text("ALTER TABLE services DROP COLUMN IF EXISTS owner"))
        await conn.execute(
            text("ALTER TABLE services DROP COLUMN IF EXISTS repository_url")
        )
        await conn.execute(
            text("ALTER TABLE services DROP COLUMN IF EXISTS dashboard_url")
        )
    print("done：已删除 service_dependencies 表和 services 的无用元数据列")


if __name__ == "__main__":
    asyncio.run(main())
