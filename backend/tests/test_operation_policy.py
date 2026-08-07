from __future__ import annotations

import unittest

from app.operations.policy import evaluate_operation
from app.operations.schemas import (
    ApprovalDecision,
    OperationContext,
    OperationSpec,
)


class OperationPolicyTest(unittest.TestCase):
    def test_read_operation_is_allowed_in_unattended_context(self) -> None:
        decision = evaluate_operation(
            OperationSpec(name="logs.search", effect="read", risk_level="low"),
            OperationContext(write_enabled=False),
        )

        self.assertEqual(decision.result, "allow")

    def test_write_operation_requires_approval_when_enabled(self) -> None:
        decision = evaluate_operation(
            OperationSpec(
                name="service.start",
                effect="write",
                risk_level="medium",
            ),
            OperationContext(write_enabled=True),
        )

        self.assertEqual(decision.result, "approval_required")

    def test_unattended_context_denies_write_operation(self) -> None:
        decision = evaluate_operation(
            OperationSpec(
                name="service.restart",
                effect="write",
                risk_level="high",
            ),
            OperationContext(write_enabled=False),
        )

        self.assertEqual(decision.result, "deny")
        self.assertIn("无人值守任务禁止", decision.reason)

    def test_user_rejection_is_preserved_in_policy_reason(self) -> None:
        decision = evaluate_operation(
            OperationSpec(
                name="service.start",
                effect="write",
                risk_level="medium",
            ),
            OperationContext(
                write_enabled=True,
                approval=ApprovalDecision(
                    approved=False,
                    reason="当前正在发布",
                ),
            ),
        )

        self.assertEqual(decision.result, "deny")
        self.assertIn("当前正在发布", decision.reason)

    def test_high_risk_operation_requires_second_confirmation(self) -> None:
        decision = evaluate_operation(
            OperationSpec(
                name="service.restart",
                effect="write",
                risk_level="high",
            ),
            OperationContext(
                write_enabled=True,
                approval=ApprovalDecision(approved=True),
            ),
        )

        self.assertEqual(decision.result, "deny")
        self.assertIn("二次确认", decision.reason)

    def test_medium_risk_operation_does_not_require_second_confirmation(self) -> None:
        decision = evaluate_operation(
            OperationSpec(
                name="service.start",
                effect="write",
                risk_level="medium",
            ),
            OperationContext(
                write_enabled=True,
                approval=ApprovalDecision(approved=True),
            ),
        )

        self.assertEqual(decision.result, "allow")

    def test_high_risk_operation_is_allowed_after_second_confirmation(self) -> None:
        decision = evaluate_operation(
            OperationSpec(
                name="service.restart",
                effect="write",
                risk_level="high",
            ),
            OperationContext(
                write_enabled=True,
                approval=ApprovalDecision(
                    approved=True,
                    second_confirmation=True,
                ),
            ),
        )

        self.assertEqual(decision.result, "allow")
