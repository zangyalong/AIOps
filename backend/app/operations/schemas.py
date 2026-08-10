"""与 Agent 框架解耦的操作、审批和策略数据模型。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Literal

OperationEffect = Literal["read", "write"]
RiskLevel = Literal["low", "medium", "high", "critical"]
PolicyResult = Literal["allow", "approval_required", "deny"]
ActorRole = Literal["viewer", "operator", "approver", "admin", "system"]
RunMode = Literal["interactive", "unattended"]
EnvironmentClass = Literal["production", "non_production", "unknown"]

_ACTOR_ROLES = frozenset({"viewer", "operator", "approver", "admin", "system"})
_RUN_MODES = frozenset({"interactive", "unattended"})
_PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})
_NON_PRODUCTION_ENVIRONMENTS = frozenset(
    {"dev", "development", "test", "testing", "qa", "staging", "stage", "nonprod"}
)


def classify_environment(value: object) -> EnvironmentClass:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in _PRODUCTION_ENVIRONMENTS:
        return "production"
    prefix = normalized.split("-", 1)[0]
    if (
        normalized in _NON_PRODUCTION_ENVIRONMENTS
        or prefix in _NON_PRODUCTION_ENVIRONMENTS
    ):
        return "non_production"
    return "unknown"


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"操作参数包含不可序列化类型：{type(value).__name__}")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class OperationSpec:
    """一项已登记操作的稳定安全属性。"""

    name: str
    effect: OperationEffect
    risk_level: RiskLevel
    environment_arg: str | None = None


@dataclass(frozen=True)
class OperationCall:
    """从模型工具调用规范化得到的操作请求。"""

    id: str
    name: str
    args: Mapping[str, Any]

    def __post_init__(self) -> None:
        frozen_args = _freeze_json(dict(self.args))
        # allow_nan=False 保证摘要在不同 JSON 实现中仍有唯一、可移植的表示。
        json.dumps(_thaw_json(frozen_args), ensure_ascii=False, allow_nan=False)
        object.__setattr__(self, "args", frozen_args)

    @classmethod
    def from_tool_call(cls, tool_call: Mapping[str, Any]) -> OperationCall:
        name = str(tool_call.get("name") or "").strip()
        if not name:
            raise ValueError("工具调用缺少名称")
        raw_args = tool_call.get("args") or {}
        if not isinstance(raw_args, Mapping):
            raise TypeError(f"工具 {name} 的参数必须是对象")
        return cls(
            id=str(tool_call.get("id") or ""),
            name=name,
            args=dict(raw_args),
        )

    def as_tool_call(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "args": self.normalized_args(),
            "type": "tool_call",
        }

    def normalized_args(self) -> dict[str, Any]:
        """返回可存入 JSONB、可交给工具校验的独立参数副本。"""
        return _thaw_json(self.args)

    def request_hash(
        self,
        spec: OperationSpec,
        context: OperationContext | None = None,
    ) -> str:
        """对操作名、安全属性、参数和可信策略上下文计算不可变审批摘要。"""
        payload = {
            "name": self.name,
            "effect": spec.effect,
            "risk_level": spec.risk_level,
            "args": self.normalized_args(),
        }
        if context is not None:
            payload["context"] = {
                "actor_id": context.actor_id,
                "actor_role": context.actor_role,
                "run_mode": context.run_mode,
                "environment": context.environment,
                "auto_approve_non_production_medium": (
                    context.auto_approve_non_production_medium
                ),
            }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ApprovalDecision:
    """用户对一次已挂起写操作作出的决议。"""

    approved: bool
    second_confirmation: bool = False
    reason: str = ""
    approver_id: str = "local-user"
    approver_role: ActorRole = "admin"
    operation_id: str = ""
    tool_call_id: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> ApprovalDecision | None:
        if value is None:
            return None
        raw_role = str(value.get("approver_role") or "admin")
        approver_role: ActorRole = raw_role if raw_role in _ACTOR_ROLES else "viewer"  # type: ignore[assignment]
        return cls(
            approved=bool(value.get("approved", False)),
            second_confirmation=bool(value.get("second_confirmation", False)),
            reason=str(value.get("reason") or ""),
            approver_id=str(value.get("approver_id") or "local-user"),
            approver_role=approver_role,
            operation_id=str(value.get("operation_id") or ""),
            tool_call_id=str(value.get("tool_call_id") or ""),
        )


@dataclass(frozen=True)
class OperationContext:
    """由可信调用方提供的操作者、环境和运行模式。"""

    actor_id: str = "anonymous"
    actor_role: ActorRole = "viewer"
    run_mode: RunMode = "unattended"
    environment: EnvironmentClass = "unknown"
    auto_approve_non_production_medium: bool = False
    approval: ApprovalDecision | None = None

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
        *,
        approval: ApprovalDecision | None = None,
    ) -> OperationContext:
        raw = value or {}
        raw_role = str(raw.get("actor_role") or "viewer")
        actor_role: ActorRole = raw_role if raw_role in _ACTOR_ROLES else "viewer"  # type: ignore[assignment]
        raw_mode = str(raw.get("run_mode") or "unattended")
        run_mode: RunMode = raw_mode if raw_mode in _RUN_MODES else "unattended"  # type: ignore[assignment]
        raw_environment = str(raw.get("environment") or "unknown")
        environment: EnvironmentClass = (
            raw_environment
            if raw_environment in {"production", "non_production", "unknown"}
            else classify_environment(raw_environment)
        )  # type: ignore[assignment]
        return cls(
            actor_id=str(raw.get("actor_id") or "anonymous"),
            actor_role=actor_role,
            run_mode=run_mode,
            environment=environment,
            auto_approve_non_production_medium=bool(
                raw.get("auto_approve_non_production_medium", False)
            ),
            approval=approval,
        )

    def for_call(
        self,
        call: OperationCall,
        *,
        environment_arg: str | None,
    ) -> OperationContext:
        if environment_arg is None:
            return self
        explicit_environment = call.args.get(environment_arg)
        if explicit_environment is None:
            return self
        return replace(
            self,
            environment=classify_environment(explicit_environment),
        )


@dataclass(frozen=True)
class PolicyDecision:
    """策略引擎对本次操作给出的确定性结果。"""

    result: PolicyResult
    reason: str = ""


@dataclass(frozen=True)
class PreparedOperation:
    """Broker 完成登记查询与策略判断后的操作。"""

    call: OperationCall
    spec: OperationSpec
    context: OperationContext
    decision: PolicyDecision
    approval_view: Mapping[str, Any] | None = None
