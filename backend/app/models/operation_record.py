from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class OperationRecord(Base, UUIDMixin):
    """写操作从待审批到执行结束的持久化审计记录。"""

    __tablename__ = "operation_records"
    __table_args__ = (
        CheckConstraint("effect = 'write'", name="ck_operation_records_effect"),
        CheckConstraint(
            "risk_level IN ('medium', 'high', 'critical')",
            name="ck_operation_records_risk_level",
        ),
        CheckConstraint(
            "status IN ('pending_approval', 'denied', 'executing', "
            "'completed', 'failed', 'expired')",
            name="ck_operation_records_status",
        ),
        CheckConstraint(
            "actor_role IN ('viewer', 'operator', 'approver', 'admin', 'system')",
            name="ck_operation_records_actor_role",
        ),
        CheckConstraint(
            "run_mode IN ('interactive', 'unattended')",
            name="ck_operation_records_run_mode",
        ),
        CheckConstraint(
            "environment IN ('production', 'non_production', 'unknown')",
            name="ck_operation_records_environment",
        ),
        CheckConstraint(
            "approver_role IS NULL OR approver_role IN "
            "('viewer', 'operator', 'approver', 'admin', 'system')",
            name="ck_operation_records_approver_role",
        ),
        UniqueConstraint(
            "chat_run_id",
            "tool_call_id",
            name="uq_operation_records_run_tool_call",
        ),
        Index("ix_operation_records_created_at", "created_at"),
        Index("ix_operation_records_status_expires", "status", "expires_at"),
    )

    chat_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_runs.id", ondelete="CASCADE"), nullable=False
    )
    tool_call_id: Mapped[str] = mapped_column(String(100), nullable=False)
    operation_name: Mapped[str] = mapped_column(String(100), nullable=False)
    effect: Mapped[str] = mapped_column(
        String(10), nullable=False, default="write", server_default="write"
    )
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    normalized_args: Mapped[dict] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_view: Mapped[dict] = mapped_column(JSONB, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    run_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    environment: Mapped[str] = mapped_column(String(30), nullable=False)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending_approval",
        server_default="pending_approval",
    )
    approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    second_confirmation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    approval_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approver_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approver_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
