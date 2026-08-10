"""统一 Operation Broker：准备操作并调度已登记执行器。"""

from __future__ import annotations

from typing import Any

from app.operations.policy import evaluate_operation
from app.operations.registry import OperationRegistry
from app.operations.schemas import (
    OperationCall,
    OperationContext,
    PolicyDecision,
    PreparedOperation,
)


class OperationNotAllowed(PermissionError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.reason or "操作未获策略允许")
        self.decision = decision


class OperationBroker:
    """统一执行入口；调用者不能绕过登记表和策略直接选择执行器。"""

    def __init__(self, registry: OperationRegistry) -> None:
        self._registry = registry

    def prepare(
        self,
        call: OperationCall,
        context: OperationContext,
    ) -> PreparedOperation:
        operation = self._registry.require(call.name)
        resolved_context = context.for_call(
            call,
            environment_arg=operation.spec.environment_arg,
        )
        decision = evaluate_operation(operation.spec, resolved_context)
        approval_view = None
        if operation.spec.effect == "write":
            builder = operation.build_approval_view
            if builder is None:
                raise RuntimeError(f"写操作缺少审批展示构造器：{call.name}")
            approval_view = dict(builder(call))
        return PreparedOperation(
            call=call,
            spec=operation.spec,
            context=resolved_context,
            decision=decision,
            approval_view=approval_view,
        )

    async def execute(
        self,
        call: OperationCall,
        context: OperationContext,
    ) -> Any:
        prepared = self.prepare(call, context)
        if prepared.decision.result != "allow":
            raise OperationNotAllowed(prepared.decision)
        operation = self._registry.require(call.name)
        return await operation.executor(call)
