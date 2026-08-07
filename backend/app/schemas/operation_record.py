from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

OperationRecordStatus = Literal[
    "pending_approval",
    "denied",
    "executing",
    "completed",
    "failed",
    "expired",
]


class OperationRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chat_run_id: uuid.UUID
    tool_call_id: str
    operation_name: str
    effect: Literal["write"]
    risk_level: Literal["medium", "high", "critical"]
    normalized_args: dict
    request_hash: str
    approval_view: dict
    actor_id: str
    actor_role: Literal["viewer", "operator", "approver", "admin", "system"]
    run_mode: Literal["interactive", "unattended"]
    environment: Literal["production", "non_production", "unknown"]
    policy_reason: str | None
    status: OperationRecordStatus
    approved: bool | None
    second_confirmation: bool | None
    approval_reason: str | None
    approver_id: str | None
    approver_role: str | None
    decision_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    result: str | None
    error_message: str | None
    expires_at: datetime
    created_at: datetime
