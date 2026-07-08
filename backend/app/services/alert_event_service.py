from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent
from app.repositories import alert_event_repository
from app.schemas.alert import AlertmanagerAlert


async def ingest_alerts(db: AsyncSession, alerts: list[AlertmanagerAlert]) -> list[str]:
    """把 Alertmanager webhook 里的告警逐条 upsert 进 alert_events 表。

    返回「本次新触发、需要自动诊断」的 fingerprint 列表（webhook 据此往队列里塞诊断作业）。
    从 labels 里取 alertname / severity / instance（Alertmanager 把这些都放在 labels 里）。
    ends_at 只在 resolved 时有意义——firing 时 Alertmanager 给的是个占位的未来/零时间，置空避免误导。
    """
    to_triage: list[str] = []
    for a in alerts:
        labels = a.labels or {}
        _, newly_firing = await alert_event_repository.upsert_by_fingerprint(
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
        if newly_firing:
            to_triage.append(a.fingerprint)
    await db.commit()  # 整个 webhook 的告警一次性提交
    return to_triage


async def list_alerts(
    db: AsyncSession, limit: int = 100, status: str | None = None
) -> list[AlertEvent]:
    return await alert_event_repository.list_recent(db, limit=limit, status=status)


async def get_alert(db: AsyncSession, fingerprint: str) -> AlertEvent | None:
    return await alert_event_repository.get_by_fingerprint(db, fingerprint)


async def save_diagnosis(db: AsyncSession, fingerprint: str, diagnosis: str) -> bool:
    """把 triage 跑出来的诊断结论回填到告警上。找不到该告警返回 False。"""
    alert = await alert_event_repository.get_by_fingerprint(db, fingerprint)
    if alert is None:
        return False
    alert.diagnosis = diagnosis
    alert.triaged_at = datetime.now(timezone.utc)
    await db.commit()
    return True
