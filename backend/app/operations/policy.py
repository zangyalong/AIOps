"""统一操作策略：只判断 allow / approval_required / deny，不执行工具。"""

from __future__ import annotations

from app.operations.schemas import (
    OperationContext,
    OperationSpec,
    PolicyDecision,
)

_SECOND_CONFIRMATION_RISKS = frozenset({"high", "critical"})
_WRITE_ROLES = frozenset({"operator", "approver", "admin"})
_APPROVER_ROLES = frozenset({"approver", "admin"})


def evaluate_operation(
    spec: OperationSpec,
    context: OperationContext,
) -> PolicyDecision:
    """按操作属性和当前审批上下文返回确定性决议。"""
    if spec.effect == "read":
        return PolicyDecision(result="allow")

    if spec.risk_level == "critical":
        return PolicyDecision(
            result="deny",
            reason="critical 级不可逆操作只能生成建议，禁止由 Agent 执行。",
        )

    if context.run_mode != "interactive":
        return PolicyDecision(
            result="deny",
            reason="无人值守任务禁止调用写工具，已拒绝执行。",
        )

    if context.actor_role not in _WRITE_ROLES:
        return PolicyDecision(
            result="deny",
            reason=f"角色 {context.actor_role} 没有发起写操作的权限，已拒绝执行。",
        )

    approval = context.approval
    if approval is None:
        if (
            context.auto_approve_non_production_medium
            and context.environment == "non_production"
            and spec.risk_level == "medium"
        ):
            return PolicyDecision(
                result="allow",
                reason="已按策略自动放行非生产环境的中风险操作。",
            )
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

    if approval.approver_role not in _APPROVER_ROLES:
        return PolicyDecision(
            result="deny",
            reason=f"角色 {approval.approver_role} 没有审批写操作的权限，已拒绝执行。",
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
