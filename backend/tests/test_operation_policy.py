from __future__ import annotations

import unittest

from app.operations.policy import evaluate_operation
from app.operations.schemas import (
    ApprovalDecision,
    OperationContext,
    OperationSpec,
    classify_environment,
)


def _interactive_context(
    *,
    role: str = "operator",
    environment: str = "production",
    approval: ApprovalDecision | None = None,
    auto_approve: bool = False,
) -> OperationContext:
    return OperationContext.from_mapping(
        {
            "actor_id": "user-1",
            "actor_role": role,
            "run_mode": "interactive",
            "environment": environment,
            "auto_approve_non_production_medium": auto_approve,
        },
        approval=approval,
    )


class OperationPolicyTest(unittest.TestCase):
    def test_read_operation_is_allowed_in_unattended_context(self) -> None:
        decision = evaluate_operation(
            OperationSpec(name="logs.search", effect="read", risk_level="low"),
            OperationContext(
                actor_id="worker",
                actor_role="system",
                run_mode="unattended",
            ),
        )

        self.assertEqual(decision.result, "allow")

    def test_write_operation_requires_approval_for_operator(self) -> None:
        decision = evaluate_operation(
            OperationSpec(
                name="service.start",
                effect="write",
                risk_level="medium",
            ),
            _interactive_context(),
        )

        self.assertEqual(decision.result, "approval_required")

    def test_unattended_context_denies_write_operation(self) -> None:
        decision = evaluate_operation(
            OperationSpec(
                name="service.restart",
                effect="write",
                risk_level="high",
            ),
            OperationContext(
                actor_id="worker",
                actor_role="system",
                run_mode="unattended",
            ),
        )

        self.assertEqual(decision.result, "deny")
        self.assertIn("无人值守任务禁止", decision.reason)

    def test_viewer_cannot_request_write_operation(self) -> None:
        decision = evaluate_operation(
            OperationSpec("service.start", "write", "medium"),
            _interactive_context(role="viewer"),
        )

        self.assertEqual(decision.result, "deny")
        self.assertIn("viewer", decision.reason)

    def test_user_rejection_is_preserved_in_policy_reason(self) -> None:
        decision = evaluate_operation(
            OperationSpec("service.start", "write", "medium"),
            _interactive_context(
                approval=ApprovalDecision(
                    approved=False,
                    reason="当前正在发布",
                )
            ),
        )

        self.assertEqual(decision.result, "deny")
        self.assertIn("当前正在发布", decision.reason)

    def test_only_approver_or_admin_can_approve_write(self) -> None:
        decision = evaluate_operation(
            OperationSpec("service.start", "write", "medium"),
            _interactive_context(
                approval=ApprovalDecision(
                    approved=True,
                    approver_id="operator-2",
                    approver_role="operator",
                )
            ),
        )

        self.assertEqual(decision.result, "deny")
        self.assertIn("没有审批", decision.reason)

    def test_high_risk_operation_requires_second_confirmation(self) -> None:
        decision = evaluate_operation(
            OperationSpec("service.restart", "write", "high"),
            _interactive_context(
                approval=ApprovalDecision(
                    approved=True,
                    approver_role="approver",
                )
            ),
        )

        self.assertEqual(decision.result, "deny")
        self.assertIn("二次确认", decision.reason)

    def test_high_risk_operation_is_allowed_after_second_confirmation(self) -> None:
        decision = evaluate_operation(
            OperationSpec("service.restart", "write", "high"),
            _interactive_context(
                approval=ApprovalDecision(
                    approved=True,
                    second_confirmation=True,
                    approver_role="approver",
                )
            ),
        )

        self.assertEqual(decision.result, "allow")

    def test_non_production_medium_can_be_auto_approved_when_enabled(self) -> None:
        decision = evaluate_operation(
            OperationSpec("catalog.register", "write", "medium"),
            _interactive_context(
                environment="non_production",
                auto_approve=True,
            ),
        )

        self.assertEqual(decision.result, "allow")
        self.assertIn("自动放行", decision.reason)

    def test_auto_approval_never_expands_to_prod_or_high_risk(self) -> None:
        production = evaluate_operation(
            OperationSpec("catalog.register", "write", "medium"),
            _interactive_context(auto_approve=True),
        )
        high_risk = evaluate_operation(
            OperationSpec("service.restart", "write", "high"),
            _interactive_context(
                environment="non_production",
                auto_approve=True,
            ),
        )

        self.assertEqual(production.result, "approval_required")
        self.assertEqual(high_risk.result, "approval_required")

    def test_critical_operation_is_always_denied(self) -> None:
        decision = evaluate_operation(
            OperationSpec("shell.execute", "write", "critical"),
            _interactive_context(
                environment="non_production",
                auto_approve=True,
                approval=ApprovalDecision(
                    approved=True,
                    second_confirmation=True,
                ),
            ),
        )

        self.assertEqual(decision.result, "deny")
        self.assertIn("只能生成建议", decision.reason)

    def test_environment_names_are_classified_conservatively(self) -> None:
        self.assertEqual(classify_environment("prod"), "production")
        self.assertEqual(classify_environment("staging-cn"), "non_production")
        self.assertEqual(classify_environment("customer-a"), "unknown")
