"""创建 Prometheus 预警规则执行器。"""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationError

from app.db.session import AsyncSessionLocal
from app.operations.schemas import OperationResult
from app.schemas.alert_rule import AlertRuleCreate
from app.services import alert_rule_service


async def create_alert_rule(
    name: str,
    expression: str,
    summary: str,
    duration: str = "0m",
    severity: Literal["info", "warning", "critical"] = "warning",
    description: str | None = None,
    labels: dict[str, str] | None = None,
    enabled: bool = True,
) -> OperationResult:
    try:
        data = AlertRuleCreate(
            name=name,
            expression=expression,
            summary=summary,
            duration=duration,
            severity=severity,
            description=description,
            labels=labels or {},
            enabled=enabled,
        )
    except ValidationError as exc:
        return OperationResult.failure(
            f"预警规则参数校验失败：{exc}", code="validation_failed"
        )

    async with AsyncSessionLocal() as db:
        try:
            rule = await alert_rule_service.create_rule(db, data)
        except alert_rule_service.AlertRuleConflict as exc:
            return OperationResult.failure(
                f"预警规则创建失败：{exc}", code="rule_conflict"
            )
        except alert_rule_service.AlertRuleApplyError as exc:
            return OperationResult.failure(
                f"预警规则已写入数据库，但同步 Prometheus 失败：{exc}",
                code="prometheus_sync_failed",
            )
        except ValueError as exc:
            return OperationResult.failure(
                f"预警规则创建失败：{exc}", code="rule_create_failed"
            )

    state = "已启用" if rule.enabled else "未启用"
    return OperationResult.success(
        f"已创建预警规则 {rule.name}（{state}，严重级别 {rule.severity}，"
        f"持续时间 {rule.duration}），并同步到 Prometheus。"
    )
