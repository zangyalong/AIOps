"""给 servers 表插入几条模拟数据，方便本地开发时有数据可查、可以让 query_servers 工具有内容可答。

用法（从 backend/ 目录下）：
    uv run python scripts/seed_servers.py

按 name 做了个简单去重：已存在同名 server 就跳过，可以放心重复跑。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 脚本是按文件路径直接跑的（不是 `python -m`），Python 默认只会把 scripts/ 自己
# 加进 sys.path，找不到上一级的 app 包，所以要手动把 backend/ 加进去。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.schemas.server import ServerCreate  # noqa: E402
from app.services import server_service  # noqa: E402

MOCK_SERVERS = [
    ServerCreate(name="web-01", ip_address="10.0.1.11", description="Nginx 反向代理 + 静态资源"),
    ServerCreate(name="web-02", ip_address="10.0.1.12", description="Nginx 反向代理 + 静态资源"),
    ServerCreate(name="app-01", ip_address="10.0.2.11", description="FastAPI 应用服务器"),
    ServerCreate(name="app-02", ip_address="10.0.2.12", description="FastAPI 应用服务器"),
    ServerCreate(name="db-primary", ip_address="10.0.3.10", description="PostgreSQL 主库"),
    ServerCreate(name="db-replica", ip_address="10.0.3.11", description="PostgreSQL 只读副本"),
    ServerCreate(name="cache-01", ip_address="10.0.4.10", description="Redis 缓存"),
    ServerCreate(name="monitor-01", ip_address="10.0.5.10", description="Prometheus + Grafana 监控"),
]


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        existing_names = {s.name for s in await server_service.list_servers(db)}

        created = 0
        for data in MOCK_SERVERS:
            if data.name in existing_names:
                print(f"跳过（已存在）: {data.name}")
                continue
            server = await server_service.create_server(db, data)
            print(f"已插入: {server.name} ({server.ip_address})")
            created += 1

        print(f"完成，共新增 {created} 条，跳过 {len(MOCK_SERVERS) - created} 条已存在的。")


if __name__ == "__main__":
    asyncio.run(seed())
