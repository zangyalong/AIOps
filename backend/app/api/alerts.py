"""告警相关 API（方向二）。

/alerts/webhook：接收 Alertmanager 推来的告警，落 alert_events 表——事件驱动的入口。
/alerts：列出已落库的告警，方便查看/调试，也给以后的前端告警页用。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.dependencies import DBSession
from app.models.alert_event import AlertEvent
from app.schemas.alert import AlertmanagerWebhook, AlertRead
from app.services import alert_event_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("/webhook")
async def receive_webhook(payload: AlertmanagerWebhook, db: DBSession) -> dict:
    """Alertmanager 的 webhook 目标。收下告警、upsert 落库。"""
    ingested = await alert_event_service.ingest_alerts(db, payload.alerts)
    return {"received": len(payload.alerts), "ingested": ingested}


@router.get("", response_model=list[AlertRead])
async def list_alerts(
    db: DBSession, status: str | None = None, limit: int = 100
) -> list[AlertEvent]:
    """列出落库的告警，可按 status（firing/resolved）过滤，默认按最近更新倒序。"""
    return await alert_event_service.list_alerts(db, limit=limit, status=status)
