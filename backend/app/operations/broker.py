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

    def _prepare_resolved(
        self,
        call: OperationCall,
        context: OperationContext,
    ) -> PreparedOperation:
        registered = self._registry.require(call.name)
        resolved_context = context.for_call(
            call,
            environment_arg=registered.spec.environment_arg,
        )
        decision = evaluate_operation(registered.spec, resolved_context)
        approval_view = None
        if registered.spec.effect == "write":
            builder = registered.build_approval_view
            if builder is None:
                raise RuntimeError(f"写操作缺少审批展示构造器：{call.name}")
            approval_view = dict(builder(call))
        return PreparedOperation(
            call=call,
            spec=registered.spec,
            context=resolved_context,
            decision=decision,
            approval_view=approval_view,
        )

    def prepare(
        self,
        call: OperationCall,
        context: OperationContext,
    ) -> PreparedOperation:
        """同步准备无语义解析器的操作；主要供纯 schema 单元测试和静态预览使用。"""
        operation = self._registry.require(call.name)
        if operation.normalize_call is not None:
            call = operation.normalize_call(call)
        if operation.resolve_call is not None:
            raise RuntimeError(f"操作 {call.name} 需要异步语义解析，请使用 aprepare")
        return self._prepare_resolved(call, context)

    async def aprepare(
        self,
        call: OperationCall,
        context: OperationContext,
    ) -> PreparedOperation:
        """在审批前完成 schema 规范化和数据库语义解析，再构造冻结操作。"""
        operation = self._registry.require(call.name)
        if operation.normalize_call is not None:
            call = operation.normalize_call(call)
        if operation.resolve_call is not None:
            call = await operation.resolve_call(call, context)
        return self._prepare_resolved(call, context)

    async def execute(
        self,
        call: OperationCall,
        context: OperationContext,
    ) -> Any:
        prepared = await self.aprepare(call, context)
        if prepared.decision.result != "allow":
            raise OperationNotAllowed(prepared.decision)
        operation = self._registry.require(call.name)
        return await operation.executor(prepared.call)

    async def execute_prepared(self, prepared: PreparedOperation) -> Any:
        """执行已经完成策略判断和审批绑定的冻结调用，不再重新解析模型参数。"""
        if prepared.decision.result != "allow":
            raise OperationNotAllowed(prepared.decision)
        operation = self._registry.require(prepared.call.name)
        return await operation.executor(prepared.call)
