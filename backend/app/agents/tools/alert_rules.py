"""通过 Agent 创建 AIOMS 托管预警规则的写工具。"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from pydantic import ValidationError

from app.db.session import AsyncSessionLocal
from app.schemas.alert_rule import AlertRuleCreate
from app.services import alert_rule_service


@tool
async def create_alert_rule(
    name: str,
    expression: str,
    summary: str,
    duration: str = "0m",
    severity: Literal["info", "warning", "critical"] = "warning",
    description: str | None = None,
    labels: dict[str, str] | None = None,
    enabled: bool = True,
) -> str:
    """创建并应用一条由 AIOMS 管理的 Prometheus 预警规则。

    仅在用户明确要求新增预警规则，并且规则名、PromQL、持续时间、严重级别和摘要已经明确时
    使用。这是写操作，必须先经过人工审批；工具会复用页面端相同的字段校验、PromQL 校验、
    数据库存储和 Prometheus 热重载流程。
    """
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
        return f"预警规则参数校验失败：{exc}"

    async with AsyncSessionLocal() as db:
        try:
            rule = await alert_rule_service.create_rule(db, data)
        except alert_rule_service.AlertRuleConflict as exc:
            return f"预警规则创建失败：{exc}"
        except alert_rule_service.AlertRuleApplyError as exc:
            return f"预警规则已写入数据库，但同步 Prometheus 失败：{exc}"
        except ValueError as exc:
            return f"预警规则创建失败：{exc}"

    state = "已启用" if rule.enabled else "未启用"
    return (
        f"已创建预警规则 {rule.name}（{state}，严重级别 {rule.severity}，"
        f"持续时间 {rule.duration}），并同步到 Prometheus。"
    )
