"""给 servers 表加 monitor_labels 列（若无）并给 docker-test-01 配上监控标签，供关联工具用。

为什么需要手动跑这个：后端启动时的 create_all() 只会建缺的表、不会给已有表加列，所以新增的
monitor_labels 列要手动 ALTER。这个脚本用 ADD COLUMN IF NOT EXISTS，幂等，可重复跑。

配的映射（docker-test-01 是本地 Docker 沙箱 127.0.0.1）：
- instance = node-exporter:9100（Prometheus 里的本地主机指标 / 告警）
- service  = aioms-fake-app（Loki 里的应用日志）
注：这是 demo 配对——指标是 Docker VM 的、日志是 fake-app 的，严格说不是同一个进程，但足以
演示"给定一台机器，把三支柱在同一时间窗对齐"的关联机制。真实环境里这两个标签会指向同一实体。

用法（从 backend/ 目录下）：
    uv run python scripts/seed_monitor_labels.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 按文件路径直接跑，需手动把 backend/ 加进 sys.path 才能 import app 包（同 seed_alerts.py）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.models.server import Server  # noqa: E402

_TARGET = "docker-test-01"
_LABELS = {"instance": "node-exporter:9100", "service": "aioms-fake-app"}


async def main() -> None:
    # 1. 加列（幂等）——create_all 不会给已有表加列，手动 ALTER
    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE servers ADD COLUMN IF NOT EXISTS monitor_labels JSONB")
        )
    print("monitor_labels 列已就绪")

    # 2. 给 docker-test-01 配监控标签（用 ORM，dict 会自动序列化进 JSONB）
    async with AsyncSessionLocal() as db:
        srv = (
            await db.execute(select(Server).where(Server.name == _TARGET))
        ).scalar_one_or_none()
        if srv is None:
            print(f"⚠️ 没找到服务器 {_TARGET}，请先确认 servers 表里有它（可跑 scripts/seed_servers.py）")
            return
        srv.monitor_labels = _LABELS
        await db.commit()
        print(f"已给 {_TARGET} 配置 monitor_labels = {_LABELS}")


if __name__ == "__main__":
    asyncio.run(main())
