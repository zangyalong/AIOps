from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.alert_rule import AlertRule
from app.repositories import alert_rule_repository
from app.schemas.alert_rule import (
    AlertRuleCreate,
    AlertRuleOverviewRead,
    AlertRuleRead,
    AlertRuleUpdate,
)
from app.services.observability_service import (
    am_get_alerts,
    prom_query,
    prom_reload,
    prom_rule_groups,
)


class AlertRuleConflict(ValueError):
    pass


class AlertRuleApplyError(RuntimeError):
    pass


_MANAGED_GROUP = "aiops-managed-alerts"
_REVISION_ANNOTATION = "aiops_revision"
_READY_RUNTIME_STATES = {"inactive", "pending", "firing"}


async def list_rules(db: AsyncSession) -> list[AlertRule]:
    return await alert_rule_repository.list_all(db)


async def get_rule(db: AsyncSession, rule_id: uuid.UUID) -> AlertRule | None:
    return await alert_rule_repository.get(db, rule_id)


async def _validate_expression(expression: str) -> None:
    try:
        await prom_query(expression)
    except Exception as exc:
        raise ValueError(f"PromQL 校验失败：{exc}") from exc


def _rule_revision(rule: AlertRule) -> str:
    payload = {
        "name": rule.name,
        "expression": rule.expression,
        "duration": rule.duration,
        "severity": rule.severity,
        "summary": rule.summary,
        "description": rule.description,
        "labels": rule.labels,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()[:12]


def _enabled_rule_revisions(rules: list[AlertRule]) -> dict[str, str]:
    return {rule.name: _rule_revision(rule) for rule in rules if rule.enabled}


def _render_rules_document(rules: list[AlertRule]) -> str:
    rendered_rules: list[dict] = []
    for rule in rules:
        if not rule.enabled:
            continue
        item: dict = {
            "alert": rule.name,
            "expr": rule.expression,
            "for": rule.duration,
            "labels": {**rule.labels, "severity": rule.severity},
            "annotations": {
                "summary": rule.summary,
                _REVISION_ANNOTATION: _rule_revision(rule),
            },
        }
        if rule.description:
            item["annotations"]["description"] = rule.description
        rendered_rules.append(item)
    document = {
        "groups": [
            {
                "name": _MANAGED_GROUP,
                "rules": rendered_rules,
            }
        ]
    }
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)


def _write_rules_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


async def sync_rules(db: AsyncSession) -> None:
    rules = await alert_rule_repository.list_all(db)
    expected_revisions = _enabled_rule_revisions(rules)
    _write_rules_file(
        settings.prometheus_rules_path,
        _render_rules_document(rules),
    )
    try:
        await prom_reload()
        await _wait_for_managed_rules(expected_revisions)
    except Exception as exc:
        raise AlertRuleApplyError(f"规则文件已更新，但 {exc}") from exc


async def create_rule(db: AsyncSession, data: AlertRuleCreate) -> AlertRule:
    if await alert_rule_repository.get_by_name(db, data.name) is not None:
        raise AlertRuleConflict(f"规则名 {data.name} 已存在")
    await _validate_expression(data.expression)
    rule = await alert_rule_repository.create(db, AlertRule(**data.model_dump()))
    await sync_rules(db)
    return rule


async def update_rule(
    db: AsyncSession, rule: AlertRule, data: AlertRuleUpdate
) -> AlertRule:
    values = data.model_dump(exclude_unset=True)
    next_name = values.get("name", rule.name)
    existing = await alert_rule_repository.get_by_name(db, next_name)
    if existing is not None and existing.id != rule.id:
        raise AlertRuleConflict(f"规则名 {next_name} 已存在")
    await _validate_expression(values.get("expression", rule.expression))
    for field, value in values.items():
        setattr(rule, field, value)
    saved = await alert_rule_repository.save(db, rule)
    await sync_rules(db)
    return saved


async def delete_rule(db: AsyncSession, rule: AlertRule) -> None:
    await alert_rule_repository.delete(db, rule)
    await sync_rules(db)


def _prometheus_runtime(
    groups: list[dict], *, group_name: str | None = None
) -> dict[str, dict]:
    runtime: dict[str, dict] = {}
    for group in groups:
        if group_name is not None and group.get("name") != group_name:
            continue
        for rule in group.get("rules") or []:
            name = str(rule.get("name") or "")
            if name:
                runtime[name] = rule
    return runtime


def _managed_rule_revisions(groups: list[dict]) -> dict[str, str]:
    revisions: dict[str, str] = {}
    for group in groups:
        if group.get("name") != _MANAGED_GROUP:
            continue
        for rule in group.get("rules") or []:
            name = str(rule.get("name") or "")
            revision = str(
                (rule.get("annotations") or {}).get(_REVISION_ANNOTATION) or ""
            )
            if name:
                revisions[name] = revision
    return revisions


async def _wait_for_managed_rules(
    expected_revisions: dict[str, str],
    *,
    timeout_seconds: float = 20.0,
    poll_interval: float = 0.1,
) -> None:
    """等 Prometheus 加载本次配置，并完成新规则的第一次状态评估。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last_revisions: dict[str, str] = {}
    last_states: dict[str, str] = {}
    last_error: Exception | None = None
    while True:
        try:
            groups = await prom_rule_groups()
            last_revisions = _managed_rule_revisions(groups)
            runtime = _prometheus_runtime(groups, group_name=_MANAGED_GROUP)
            last_states = {
                name: str(rule.get("state") or "unknown")
                for name, rule in runtime.items()
            }
            last_error = None
            states_ready = all(
                last_states.get(name) in _READY_RUNTIME_STATES
                for name in expected_revisions
            )
            if last_revisions == expected_revisions and states_ready:
                return
        except Exception as exc:  # noqa: BLE001 - 短暂不可用时继续轮询
            last_error = exc
        if loop.time() >= deadline:
            detail = (
                str(last_error)
                if last_error
                else (
                    f"期望版本 {expected_revisions}，实际版本 {last_revisions}，"
                    f"实际状态 {last_states}"
                )
            )
            raise RuntimeError(
                f"Prometheus 热重载后规则状态在 {timeout_seconds:g}s 内未就绪：{detail}"
            )
        await asyncio.sleep(poll_interval)


async def get_overview(db: AsyncSession) -> AlertRuleOverviewRead:
    rules = await alert_rule_repository.list_all(db)
    prometheus_message: str | None = None
    alertmanager_message: str | None = None
    runtime: dict[str, dict] = {}
    prometheus_groups: list[dict] = []
    active_by_name: dict[str, int] = {}
    active_total = 0
    try:
        prometheus_groups = await prom_rule_groups()
        runtime = _prometheus_runtime(prometheus_groups, group_name=_MANAGED_GROUP)
    except Exception as exc:  # noqa: BLE001
        prometheus_message = str(exc)
    try:
        active_alerts = await am_get_alerts()
        active_total = len(active_alerts)
        for alert in active_alerts:
            name = str((alert.get("labels") or {}).get("alertname") or "")
            if name:
                active_by_name[name] = active_by_name.get(name, 0) + 1
    except Exception as exc:  # noqa: BLE001
        alertmanager_message = str(exc)

    rows: list[AlertRuleRead] = []
    for rule in rules:
        current = runtime.get(rule.name) or {}
        if not rule.enabled:
            state = "disabled"
            health = "disabled"
            last_error = None
        else:
            state = str(current.get("state") or "unknown")
            if state not in _READY_RUNTIME_STATES:
                state = "unknown"
            health = str(current.get("health") or "unknown")
            last_error = str(current.get("lastError") or "") or None
        rows.append(
            AlertRuleRead.model_validate(rule).model_copy(
                update={
                    "runtime_state": state,
                    "health": health,
                    "last_error": last_error,
                    "active_alerts": active_by_name.get(rule.name, 0),
                }
            )
        )
    return AlertRuleOverviewRead(
        rules=rows,
        active_alerts_total=active_total,
        prometheus_message=prometheus_message,
        alertmanager_message=alertmanager_message,
    )
