"""与 Agent 框架解耦的操作注册表。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.operations.schemas import OperationCall, OperationSpec

OperationExecutor = Callable[[OperationCall], Awaitable[Any]]
ApprovalViewBuilder = Callable[[OperationCall], Mapping[str, Any]]


@dataclass(frozen=True)
class RegisteredOperation:
    spec: OperationSpec
    executor: OperationExecutor
    build_approval_view: ApprovalViewBuilder | None = None


class OperationRegistry:
    """保存操作安全属性、执行器和审批展示构造器。"""

    def __init__(self, operations: Iterable[RegisteredOperation]) -> None:
        indexed: dict[str, RegisteredOperation] = {}
        for operation in operations:
            name = operation.spec.name
            if name in indexed:
                raise RuntimeError(f"操作重复注册：{name}")
            if (
                operation.spec.effect == "write"
                and operation.build_approval_view is None
            ):
                raise RuntimeError(f"写操作缺少审批展示构造器：{name}")
            indexed[name] = operation
        self._operations: Mapping[str, RegisteredOperation] = MappingProxyType(indexed)

    def get(self, name: str) -> RegisteredOperation | None:
        return self._operations.get(name)

    def require(self, name: str) -> RegisteredOperation:
        operation = self.get(name)
        if operation is None:
            raise LookupError(f"操作 {name} 未登记")
        return operation

    @property
    def specs(self) -> Mapping[str, OperationSpec]:
        return MappingProxyType(
            {name: operation.spec for name, operation in self._operations.items()}
        )
