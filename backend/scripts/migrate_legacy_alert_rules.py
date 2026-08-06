"""把旧版内置的三条演示规则迁入 AIOMS 管理。

该脚本幂等执行：先补 alert_rules.labels 列，再只插入数据库中尚不存在的规则名，
最后重写统一托管规则文件并请求 Prometheus 热重载。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import AsyncSessionLocal, engine
from app.models.alert_rule import AlertRule
from app.models.base import Base
from app.repositories import alert_rule_repository
from app.services import alert_rule_service

LEGACY_RULES = (
    {
        "name": "Watchdog",
        "expression": "vector(1)",
        "duration": "0m",
        "severity": "info",
        "summary": "告警链路自检（始终触发，用于确认 Alertmanager 通路正常）",
        "description": None,
        "labels": {},
        "enabled": True,
    },
    {
        "name": "NodeMemoryUsageHigh",
        "expression": (
            "(1 - node_memory_MemAvailable_bytes / "
            "node_memory_MemTotal_bytes) * 100 > 1"
        ),
        "duration": "0m",
        "severity": "warning",
        "summary": "节点内存使用率偏高（demo 阈值 1%）",
        "description": (
            '{{ $labels.instance }} 内存使用率约 {{ $value | printf "%.1f" }}%'
        ),
        "labels": {},
        "enabled": True,
    },
    {
        "name": "DiskWillFillSoon",
        "expression": "vector(1)",
        "duration": "0m",
        "severity": "critical",
        "summary": "磁盘预计 5 天内写满（synthetic demo 告警，恒触发）",
        "description": None,
        "labels": {"instance": "db-01:9100"},
        "enabled": True,
    },
)


async def migrate() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text(
                "ALTER TABLE alert_rules "
                "ADD COLUMN IF NOT EXISTS labels JSONB NOT NULL "
                "DEFAULT '{}'::jsonb"
            )
        )
    inserted = 0
    async with AsyncSessionLocal() as db:
        for values in LEGACY_RULES:
            if await alert_rule_repository.get_by_name(db, values["name"]) is None:
                db.add(AlertRule(**values))
                inserted += 1
        await db.commit()
        try:
            await alert_rule_service.sync_rules(db)
            reload_message = "Prometheus 已热重载"
        except alert_rule_service.AlertRuleApplyError as exc:
            reload_message = str(exc)

    print(f"迁移完成：新增 {inserted} 条规则；{reload_message}")


if __name__ == "__main__":
    asyncio.run(migrate())
