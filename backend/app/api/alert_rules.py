from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import DBSession
from app.models.alert_rule import AlertRule
from app.schemas.alert_rule import (
    AlertRuleCreate,
    AlertRuleMutationRead,
    AlertRuleOverviewRead,
    AlertRuleUpdate,
)
from app.services import alert_rule_service

router = APIRouter(prefix="/alert-rules", tags=["alert-rules"])


async def _get_rule_or_404(db: AsyncSession, rule_id: uuid.UUID) -> AlertRule:
    rule = await alert_rule_service.get_rule(db, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="alert rule not found")
    return rule


def _raise_rule_error(exc: Exception) -> None:
    if isinstance(exc, alert_rule_service.AlertRuleConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, alert_rule_service.AlertRuleApplyError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.get("", response_model=AlertRuleOverviewRead)
async def list_alert_rules(db: DBSession, response: Response) -> AlertRuleOverviewRead:
    response.headers["Cache-Control"] = "no-store"
    return await alert_rule_service.get_overview(db)


@router.post("", response_model=AlertRuleMutationRead, status_code=201)
async def create_alert_rule(
    data: AlertRuleCreate, db: DBSession
) -> AlertRuleMutationRead:
    try:
        rule = await alert_rule_service.create_rule(db, data)
    except Exception as exc:  # noqa: BLE001 - 统一映射业务错误
        _raise_rule_error(exc)
    return AlertRuleMutationRead(
        message=f"规则 {rule.name} 已创建并同步到 Prometheus",
        overview=await alert_rule_service.get_overview(db),
    )


@router.post("/apply", response_model=AlertRuleMutationRead)
async def apply_alert_rules(db: DBSession) -> AlertRuleMutationRead:
    try:
        await alert_rule_service.sync_rules(db)
    except Exception as exc:  # noqa: BLE001
        _raise_rule_error(exc)
    return AlertRuleMutationRead(
        message="规则已同步并热重载 Prometheus",
        overview=await alert_rule_service.get_overview(db),
    )


@router.patch("/{rule_id}", response_model=AlertRuleMutationRead)
async def update_alert_rule(
    rule_id: uuid.UUID, data: AlertRuleUpdate, db: DBSession
) -> AlertRuleMutationRead:
    rule = await _get_rule_or_404(db, rule_id)
    try:
        saved = await alert_rule_service.update_rule(db, rule, data)
    except Exception as exc:  # noqa: BLE001
        _raise_rule_error(exc)
    return AlertRuleMutationRead(
        message=f"规则 {saved.name} 已更新并同步到 Prometheus",
        overview=await alert_rule_service.get_overview(db),
    )


@router.delete("/{rule_id}", response_model=AlertRuleMutationRead)
async def delete_alert_rule(rule_id: uuid.UUID, db: DBSession) -> AlertRuleMutationRead:
    rule = await _get_rule_or_404(db, rule_id)
    name = rule.name
    try:
        await alert_rule_service.delete_rule(db, rule)
    except Exception as exc:  # noqa: BLE001
        _raise_rule_error(exc)
    return AlertRuleMutationRead(
        message=f"规则 {name} 已删除并从 Prometheus 移除",
        overview=await alert_rule_service.get_overview(db),
    )
