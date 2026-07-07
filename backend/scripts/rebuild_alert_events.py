"""重塑 alert_events 表：drop 掉旧结构、用新 model 重建（方向二）。

旧表装的是 seed_alerts.py 的 mock 数据（P1/P2/P3 + message），已不再用；新结构接 Alertmanager
webhook 推来的真实告警。因为 create_all 只建缺的表、不会改已有表结构，所以要手动 drop 重建。
表里都是可丢的 mock 数据，drop 无所谓。

用法（从 backend/ 目录下）：
    uv run python scripts/rebuild_alert_events.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import configure_mappers  # noqa: E402

from app.db.session import engine  # noqa: E402

# 导入 server 让 FK 目标 servers 表进 metadata；导入 alert_event 拿到 AlertEvent。
from app.models import alert_event, server  # noqa: E402, F401
from app.models.alert_event import AlertEvent  # noqa: E402


async def main() -> None:
    # 单独建一张表时，要先 configure_mappers 把 Mapped 注解里的类型（如 server_id 的 UUID）解析出来，
    # 否则 FK 列会是 NullType、生成不了 DDL。app 启动的 create_all 上下文完整，不用手动调这个。
    configure_mappers()
    async with engine.begin() as conn:
        await conn.run_sync(AlertEvent.__table__.drop, checkfirst=True)
        await conn.run_sync(AlertEvent.__table__.create, checkfirst=True)
    print("alert_events 已按新结构重建（旧 mock 数据已清空）")


if __name__ == "__main__":
    asyncio.run(main())
