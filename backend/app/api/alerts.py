"""告警相关 API（方向二）。

/alerts/webhook：接收 Alertmanager 推来的告警，落 alert_events 表——事件驱动的入口。
/alerts：列出已落库的告警，方便查看/调试，也给以后的前端告警页用。
"""

from __future__ import annotations

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Request

from app.core.config import settings
from app.dependencies import DBSession
from app.models.alert_event import AlertEvent
from app.schemas.alert import AlertmanagerWebhook, AlertRead
from app.services import alert_event_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


async def _get_arq_pool(request: Request):
    """拿 arq 连接池；启动时 Redis 没起导致 pool 为 None 的话，这里惰性补建一次。"""
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        try:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            request.app.state.arq_pool = pool
        except Exception:  # noqa: BLE001 —— Redis 仍连不上就返回 None，跳过入队
            pool = None
    return pool


@router.post("/webhook")
async def receive_webhook(payload: AlertmanagerWebhook, db: DBSession, request: Request) -> dict:
    """Alertmanager 的 webhook 目标：收下告警 upsert 落库，并把「新触发」的告警塞进队列做自动诊断。"""
    to_triage = await alert_event_service.ingest_alerts(db, payload.alerts)

    # 新触发的告警入队，交给 arq worker 后台诊断。webhook 本身不等诊断跑完，立刻返回。
    pool = await _get_arq_pool(request)
    enqueued = 0
    if pool is not None:
        for fingerprint in to_triage:
            await pool.enqueue_job("triage_alert", fingerprint)
            enqueued += 1

    return {
        "received": len(payload.alerts),
        "newly_firing": len(to_triage),
        "triage_enqueued": enqueued,
    }


@router.get("", response_model=list[AlertRead])
async def list_alerts(
    db: DBSession, status: str | None = None, limit: int = 100
) -> list[AlertEvent]:
    """列出落库的告警，可按 status（firing/resolved）过滤，默认按最近更新倒序。"""
    return await alert_event_service.list_alerts(db, limit=limit, status=status)
