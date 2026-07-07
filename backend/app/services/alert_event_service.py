from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent
from app.repositories import alert_event_repository
from app.schemas.alert import AlertmanagerAlert


async def ingest_alerts(db: AsyncSession, alerts: list[AlertmanagerAlert]) -> int:
    """把 Alertmanager webhook 里的告警逐条 upsert 进 alert_events 表，返回处理条数。

    从 labels 里取 alertname / severity / instance（Alertmanager 把这些都放在 labels 里）。
    ends_at 只在 resolved 时有意义——firing 时 Alertmanager 给的是个占位的未来/零时间，置空避免误导。
    """
    for a in alerts:
        labels = a.labels or {}
        await alert_event_repository.upsert_by_fingerprint(
            db,
            fingerprint=a.fingerprint,
            alertname=labels.get("alertname", "（未知告警）"),
            severity=labels.get("severity", "none"),
            status=a.status,
            summary=a.annotations.get("summary") if a.annotations else None,
            labels=labels,
            instance=labels.get("instance"),
            starts_at=a.starts_at,
            ends_at=a.ends_at if a.status == "resolved" else None,
        )
    await db.commit()  # 整个 webhook 的告警一次性提交
    return len(alerts)


async def list_alerts(
    db: AsyncSession, limit: int = 100, status: str | None = None
) -> list[AlertEvent]:
    return await alert_event_repository.list_recent(db, limit=limit, status=status)
