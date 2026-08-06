from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent
from app.models.incident import RESOLVED
from app.repositories import (
    alert_event_repository,
    alert_occurrence_repository,
    incident_repository,
)
from app.schemas.alert import AlertmanagerAlert
from app.services import correlation


async def ingest_alerts(db: AsyncSession, alerts: list[AlertmanagerAlert]) -> list[uuid.UUID]:
    """upsert 告警并把它们挂到「事件（incidents）」上。返回本次**新建**、需要自动诊断的 incident_id 列表。

    - firing 告警：算 group_key（规则没命中就用 fingerprint 兜底、独立成事件）→ 找该 group 的活跃事件；
      没有就新建一个事件（并入队诊断），有就挂上去（不重复诊断）。
    - resolved 告警：把状态落库；若它所属事件下的告警全 resolved 了，事件自动置 resolved。
    """
    to_triage: list[uuid.UUID] = []
    for a in alerts:
        labels = a.labels or {}
        group_key = correlation.compute_group_key(labels) or f"fp:{a.fingerprint}"
        alert, _ = await alert_event_repository.upsert_by_fingerprint(
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
            group_key=group_key,
        )
        # session 是 autoflush=False，flush 让这条落到 DB（未提交），好让后续查询 / 同批告警看得到它。
        await db.flush()

        if a.status == "firing":
            # 并发提醒（TODO，非当前必须）：两个 webhook 几乎同时带同一新 group_key 的首条 firing 时，
            # 可能各自查到 None、各建一个事件，破坏「每个 group_key 至多一个活跃事件」。本项目单 uvicorn +
            # 单 worker，且 Alertmanager 把一批告警合成一个 webhook 顺序处理，实际几乎碰不到。真要扛并发：
            # 给 incidents 加 (group_key) WHERE status!='resolved' 的部分唯一索引 + 撞了重查，或对 group_key
            # 上事务级 advisory lock。
            incident = await incident_repository.get_active_by_group_key(db, group_key)
            if incident is None:
                # 这个 group 当前没有活跃事件 → 新事件（新建 + 入队诊断）
                incident = await incident_repository.create(
                    db,
                    group_key=group_key,
                    alertname=labels.get("alertname", "（未知告警）"),
                    severity=labels.get("severity", "none"),
                    instance=labels.get("instance"),
                    summary=a.annotations.get("summary") if a.annotations else None,
                )
                await db.flush()
                to_triage.append(incident.id)
            alert.incident_id = incident.id
            await db.flush()
            await alert_occurrence_repository.upsert_for_incident(
                db,
                alert=alert,
                incident_id=incident.id,
                status="firing",
            )
            await db.flush()
        elif alert.incident_id is not None:
            await alert_occurrence_repository.upsert_for_incident(
                db,
                alert=alert,
                incident_id=alert.incident_id,
                status="resolved",
                ends_at=alert.ends_at,
            )
            await db.flush()
            # resolved：若所属事件下的告警都不再 firing，事件自动 resolve
            if await incident_repository.count_firing_alerts(db, alert.incident_id) == 0:
                incident = await incident_repository.get(db, alert.incident_id)
                if incident is not None and incident.status != RESOLVED:
                    incident.status = RESOLVED
                    incident.resolved_at = datetime.now(timezone.utc)

    await db.commit()
    return to_triage


async def list_alerts(
    db: AsyncSession, limit: int = 100, status: str | None = None
) -> list[AlertEvent]:
    return await alert_event_repository.list_recent(db, limit=limit, status=status)
