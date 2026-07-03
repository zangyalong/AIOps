"""给 alert_events 表插入几条模拟告警，方便本地开发时有数据可查、可以让 query_alerts 工具有内容可答。

依赖 servers 表已经有数据（先跑 scripts/seed_servers.py）——告警是挂在具体服务器名字下面的，
名字在 servers 表里找不到就跳过那条、打印警告，不会中断整个脚本。

用法（从 backend/ 目录下）：
    uv run python scripts/seed_alerts.py

按 (服务器, severity, message) 三元组去重：已存在同样内容的告警就跳过，可以放心重复跑。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 脚本是按文件路径直接跑的（不是 `python -m`），Python 默认只会把 scripts/ 自己
# 加进 sys.path，找不到上一级的 app 包，所以要手动把 backend/ 加进去。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services import alert_event_service, server_service  # noqa: E402

# (服务器名, severity, message)
MOCK_ALERTS = [
    ("web-01", "P2", "CPU 使用率持续 5 分钟超过 85%"),
    ("web-01", "P3", "磁盘使用率达到 78%，建议关注"),
    ("web-02", "P3", "近 10 分钟 5xx 错误率上升到 2%"),
    ("app-01", "P1", "健康检查连续 3 次失败，实例可能已经不可用"),
    ("app-02", "P2", "GC 停顿时间超过 500ms，响应延迟升高"),
    ("db-primary", "P1", "连接数逼近 max_connections 上限（95%）"),
    ("db-replica", "P3", "主从复制延迟达到 12s"),
    ("cache-01", "P2", "内存使用率超过 90%，可能触发淘汰"),
    ("monitor-01", "P3", "磁盘空间剩余不足 15%，注意日志/指标存储"),
]


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        servers = await server_service.list_servers(db)
        server_by_name = {s.name: s for s in servers}

        created = skipped = missing = 0
        for server_name, severity, message in MOCK_ALERTS:
            server = server_by_name.get(server_name)
            if server is None:
                print(f"跳过（servers 表里没有 {server_name}，先跑 seed_servers.py）: {message}")
                missing += 1
                continue

            if await alert_event_service.alert_exists(db, server.id, severity, message):
                print(f"跳过（已存在）: [{severity}] {server_name} - {message}")
                skipped += 1
                continue

            await alert_event_service.create_alert(db, server.id, severity, message)
            print(f"已插入: [{severity}] {server_name} - {message}")
            created += 1

        print(f"完成，共新增 {created} 条，跳过 {skipped} 条已存在的，{missing} 条因服务器不存在被跳过。")


if __name__ == "__main__":
    asyncio.run(seed())
