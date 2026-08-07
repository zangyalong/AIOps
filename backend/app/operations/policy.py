"""统一操作策略：只判断 allow / approval_required / deny，不执行工具。"""

from __future__ import annotations

from app.operations.schemas import (
    OperationContext,
    OperationSpec,
    PolicyDecision,
)

_SECOND_CONFIRMATION_RISKS = frozenset({"high", "critical"})


def evaluate_operation(
    spec: OperationSpec,
    context: OperationContext,
) -> PolicyDecision:
    """按操作属性和当前审批上下文返回确定性决议。"""
    if spec.effect == "read":
        return PolicyDecision(result="allow")

    if not context.write_enabled:
        return PolicyDecision(
            result="deny",
            reason="无人值守任务禁止调用写工具，已拒绝执行。",
        )

    approval = context.approval
    if approval is None:
        return PolicyDecision(
            result="approval_required",
            reason="写工具缺少人工审批决议，已拒绝执行。",
        )

    if not approval.approved:
        reason = approval.reason.strip() or "用户拒绝了本次操作"
        return PolicyDecision(
            result="deny",
            reason=f"用户拒绝执行该操作（{reason}）。请不要重试。",
        )

    if (
        spec.risk_level in _SECOND_CONFIRMATION_RISKS
        and not approval.second_confirmation
    ):
        return PolicyDecision(
            result="deny",
            reason="高风险写操作缺少详情弹窗二次确认，已拒绝执行。",
        )

    return PolicyDecision(result="allow")
