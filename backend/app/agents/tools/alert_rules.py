"""创建预警规则的 Agent 工具定义。"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool

from app.operations.executors import alert_rule
from app.operations.schemas import OperationResult
from app.schemas.alert_rule import AlertRuleCreate


@tool(args_schema=AlertRuleCreate)
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
    """创建并应用一条由 AIOps 管理的 Prometheus 预警规则。

    仅在用户明确要求新增预警规则，并且规则名、PromQL、持续时间、严重级别和摘要已经明确时
    使用。name 是仅含英文字母、数字和下划线的机器标识符（例如 NodeCpuUsageHigh），中文展示
    文本应写入 summary。这是写操作，必须先经过人工审批。
    """
    return await alert_rule.create_alert_rule(
        name=name,
        expression=expression,
        summary=summary,
        duration=duration,
        severity=severity,
        description=description,
        labels=labels,
        enabled=enabled,
    )
