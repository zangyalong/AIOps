"""Agent 工具目录：集中声明读写边界和写操作审批元数据。

新增工具时必须在这里明确归入 READ_TOOLS 或 WRITE_TOOL_SPECS。两类名称不允许重叠；写工具
必须有风险等级和审批卡片构造器。图编排只通过本目录判断工具权限，不再维护手写名称集合。
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from langchain_core.tools import BaseTool

from app.agents.tools.actions import restart_service, start_service, stop_service
from app.agents.tools.alert_rules import create_alert_rule
from app.agents.tools.deployment import deploy_server_environment
from app.agents.tools.investigation import investigate
from app.agents.tools.server_files import (
    apply_nginx_config,
    patch_server_config,
    update_server_file,
)
from app.agents.tools.server_log_config import (
    apply_server_log_config,
)
from app.agents.tools.service_discovery import (
    register_server_services,
)
from app.operations.broker import OperationBroker
from app.operations.registry import OperationRegistry, RegisteredOperation
from app.operations.schemas import (
    OperationCall,
    OperationContext,
    OperationSpec,
    RiskLevel,
)

ActionDetailsBuilder = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class WriteToolSpec:
    """写工具除实现本身外，还必须声明风险等级和人工审批卡片内容。"""

    tool: BaseTool
    risk_level: RiskLevel
    build_action_details: ActionDetailsBuilder

    def pending_action(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        args = dict(tool_call.get("args") or {})
        details = self.build_action_details(args)
        return {
            "tool_call_id": tool_call.get("id", ""),
            "tool": self.tool.name,
            "risk_level": self.risk_level,
            "args": args,
            **details,
        }


def _service_control_action(
    args: dict[str, Any], *, action: Literal["start", "stop", "restart"]
) -> dict[str, Any]:
    server = str(args.get("server_name") or "?")
    service = str(args.get("service_name") or "?")
    runtime_type = str(args.get("runtime_type") or "systemd")
    label = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
    return {
        "kind": f"service_{action}",
        "title": f"{label} {server} 上的 {runtime_type} 服务 {service}",
        "server_name": server,
        "service_name": service,
        "runtime_type": runtime_type,
        "command": (
            "sudo -n -- /usr/local/sbin/aioms-service-control "
            f"{action} {shlex.quote(runtime_type)} {shlex.quote(service)}"
        ),
    }


def _start_service_action(args: dict[str, Any]) -> dict[str, Any]:
    return _service_control_action(args, action="start")


def _stop_service_action(args: dict[str, Any]) -> dict[str, Any]:
    return _service_control_action(args, action="stop")


def _restart_service_action(args: dict[str, Any]) -> dict[str, Any]:
    return _service_control_action(args, action="restart")


def _create_alert_rule_action(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "?")
    expression = str(args.get("expression") or "?")
    severity = str(args.get("severity") or "warning")
    duration = str(args.get("duration") or "0m")
    return {
        "kind": "alert_rule_create",
        "title": f"创建预警规则 {name}",
        "rule_name": name,
        "severity": severity,
        "command": f"{expression}；持续 {duration}；严重级别 {severity}",
    }


def _register_server_services_action(args: dict[str, Any]) -> dict[str, Any]:
    server = str(args.get("server_name") or "?")
    environment = str(args.get("environment") or "prod")
    services = [item for item in (args.get("services") or []) if isinstance(item, dict)]
    mappings = [
        (
            f"{item.get('service_name', '?')} ← "
            f"{item.get('runtime_type', '?')}/{item.get('runtime_name', '?')}"
        )
        for item in services
    ]
    summary = "；".join(mappings) or "（没有服务明细）"
    return {
        "kind": "service_catalog_registration",
        "title": f"注册 {len(services)} 个服务目录项",
        "server_name": server,
        "environment": environment,
        "service_names": [str(item.get("service_name") or "?") for item in services],
        "command": f"注册到 {environment} 环境：{summary}",
    }


def _apply_server_log_config_action(args: dict[str, Any]) -> dict[str, Any]:
    server = str(args.get("server_name") or "?")
    mappings = [item for item in (args.get("mappings") or []) if isinstance(item, dict)]
    sources = [
        (
            f"{item.get('service_name', '?')} ← "
            f"{item.get('runtime_type', '?')}/{item.get('runtime_name', '?')} "
            f"({item.get('log_source', '?')})"
        )
        for item in mappings
        if item.get("log_source") != "none"
    ]
    return {
        "kind": "server_log_config_apply",
        "title": f"应用 {server} 的 {len(sources)} 个日志源并重载 Alloy",
        "server_name": server,
        "service_names": [str(item.get("service_name") or "?") for item in mappings],
        "command": "；".join(sources) or "（没有启用的日志源）",
        "preview_hash": str(args.get("preview_hash") or ""),
    }


def _deploy_server_environment_action(args: dict[str, Any]) -> dict[str, Any]:
    server = str(args.get("server_name") or "?")
    return {
        "kind": "server_environment_deploy",
        "title": f"自动部署 {server} 的环境配置",
        "server_name": server,
        "command": (
            "上传服务器页面保存的组件配置，并调用固定入口 "
            "aioms-target-deploy apply-config"
        ),
    }


def _update_server_file_action(args: dict[str, Any]) -> dict[str, Any]:
    server = str(args.get("server_name") or "?")
    path = str(args.get("path") or "?")
    preview_diff = str(args.get("preview_diff") or "（缺少差异预览）")
    return {
        "kind": "server_file_update",
        "title": f"更新 {server} 上的文件 {path}",
        "server_name": server,
        "path": path,
        "command": preview_diff,
        "preview_hash": str(args.get("preview_hash") or ""),
    }


def _patch_server_config_action(args: dict[str, Any]) -> dict[str, Any]:
    server = str(args.get("server_name") or "?")
    path = str(args.get("path") or "?")
    selector = str(args.get("selector") or "?")
    preview_diff = str(args.get("preview_diff") or "（缺少字段补丁预览）")
    return {
        "kind": "server_config_patch",
        "title": f"修改 {server} 上 {path} 的配置项 {selector}",
        "server_name": server,
        "path": path,
        "selector": selector,
        "command": preview_diff,
        "preview_hash": str(args.get("preview_hash") or ""),
    }


def _apply_nginx_config_action(args: dict[str, Any]) -> dict[str, Any]:
    server = str(args.get("server_name") or "?")
    path = str(args.get("path") or "?")
    preview_diff = str(args.get("preview_diff") or "（缺少 Nginx 差异预览）")
    return {
        "kind": "nginx_config_apply",
        "title": f"发布并重载 {server} 上的 Nginx 配置 {path}",
        "server_name": server,
        "path": path,
        "command": f"{preview_diff}\n校验通过后 reload；失败自动恢复备份",
        "preview_hash": str(args.get("preview_hash") or ""),
    }


READ_TOOLS: tuple[BaseTool, ...] = (investigate,)

_WRITE_SPECS = (
    WriteToolSpec(
        tool=register_server_services,
        risk_level="medium",
        build_action_details=_register_server_services_action,
    ),
    WriteToolSpec(
        tool=create_alert_rule,
        risk_level="medium",
        build_action_details=_create_alert_rule_action,
    ),
    WriteToolSpec(
        tool=start_service,
        risk_level="medium",
        build_action_details=_start_service_action,
    ),
    WriteToolSpec(
        tool=stop_service,
        risk_level="high",
        build_action_details=_stop_service_action,
    ),
    WriteToolSpec(
        tool=restart_service,
        risk_level="high",
        build_action_details=_restart_service_action,
    ),
    WriteToolSpec(
        tool=apply_server_log_config,
        risk_level="high",
        build_action_details=_apply_server_log_config_action,
    ),
    WriteToolSpec(
        tool=deploy_server_environment,
        risk_level="high",
        build_action_details=_deploy_server_environment_action,
    ),
    WriteToolSpec(
        tool=update_server_file,
        risk_level="high",
        build_action_details=_update_server_file_action,
    ),
    WriteToolSpec(
        tool=patch_server_config,
        risk_level="medium",
        build_action_details=_patch_server_config_action,
    ),
    WriteToolSpec(
        tool=apply_nginx_config,
        risk_level="high",
        build_action_details=_apply_nginx_config_action,
    ),
)


def _index_tools(tools: Iterable[BaseTool], category: str) -> dict[str, BaseTool]:
    result: dict[str, BaseTool] = {}
    for tool in tools:
        if tool.name in result:
            raise RuntimeError(f"{category}工具重复注册：{tool.name}")
        result[tool.name] = tool
    return result


_read_by_name = _index_tools(READ_TOOLS, "读")
_write_specs_by_name = {spec.tool.name: spec for spec in _WRITE_SPECS}
if len(_write_specs_by_name) != len(_WRITE_SPECS):
    raise RuntimeError("写工具重复注册")
overlap = set(_read_by_name) & set(_write_specs_by_name)
if overlap:
    raise RuntimeError(f"工具不能同时注册为读和写：{sorted(overlap)}")

READ_TOOLS_BY_NAME: Mapping[str, BaseTool] = MappingProxyType(_read_by_name)
WRITE_TOOL_SPECS: Mapping[str, WriteToolSpec] = MappingProxyType(_write_specs_by_name)
WRITE_TOOLS_BY_NAME: Mapping[str, BaseTool] = MappingProxyType(
    {name: spec.tool for name, spec in _write_specs_by_name.items()}
)
TOOLS_BY_NAME: Mapping[str, BaseTool] = MappingProxyType(
    {**_read_by_name, **dict(WRITE_TOOLS_BY_NAME)}
)
WRITE_TOOL_NAMES = frozenset(WRITE_TOOLS_BY_NAME)


def _registered_read_operation(tool: BaseTool) -> RegisteredOperation:
    async def execute(call: OperationCall) -> Any:
        return await tool.ainvoke(call.normalized_args())

    return RegisteredOperation(
        spec=OperationSpec(
            name=tool.name,
            effect="read",
            risk_level="low",
            audit_execution=True,
        ),
        executor=execute,
    )


def _registered_write_operation(write_spec: WriteToolSpec) -> RegisteredOperation:
    async def execute(call: OperationCall) -> Any:
        return await write_spec.tool.ainvoke(call.normalized_args())

    def build_approval_view(call: OperationCall) -> Mapping[str, Any]:
        return write_spec.pending_action(call.as_tool_call())

    return RegisteredOperation(
        spec=OperationSpec(
            name=write_spec.tool.name,
            effect="write",
            risk_level=write_spec.risk_level,
            environment_arg=(
                "environment"
                if write_spec.tool.name == register_server_services.name
                else None
            ),
        ),
        executor=execute,
        build_approval_view=build_approval_view,
    )


OPERATION_REGISTRY = OperationRegistry(
    [
        *(_registered_read_operation(tool) for tool in READ_TOOLS_BY_NAME.values()),
        *(
            _registered_write_operation(write_spec)
            for write_spec in WRITE_TOOL_SPECS.values()
        ),
    ]
)
OPERATION_BROKER = OperationBroker(OPERATION_REGISTRY)
OPERATION_SPECS: Mapping[str, OperationSpec] = OPERATION_REGISTRY.specs


def build_scoped_operation_broker(tools: Iterable[BaseTool]) -> OperationBroker:
    """为当前 Ops Agent 获配的工具构造 Broker，安全元数据仍只取自中央注册表。"""
    scoped: list[RegisteredOperation] = []
    for tool in tools:
        registered = OPERATION_REGISTRY.require(tool.name)

        async def execute(call: OperationCall, current_tool: BaseTool = tool) -> Any:
            return await current_tool.ainvoke(call.normalized_args())

        scoped.append(
            RegisteredOperation(
                spec=registered.spec,
                executor=execute,
                build_approval_view=registered.build_approval_view,
            )
        )
    return OperationBroker(OperationRegistry(scoped))


def get_operation_spec(tool_name: str) -> OperationSpec | None:
    return OPERATION_SPECS.get(tool_name)


def is_write_tool(tool_name: str) -> bool:
    spec = get_operation_spec(tool_name)
    return spec is not None and spec.effect == "write"


def describe_write_action(tool_call: dict[str, Any]) -> dict[str, Any]:
    call = OperationCall.from_tool_call(tool_call)
    spec = get_operation_spec(call.name)
    if spec is None or spec.effect != "write":
        raise ValueError(f"{call.name or '未知工具'} 不是已注册的写工具")
    prepared = OPERATION_BROKER.prepare(
        call,
        OperationContext(
            actor_id="catalog-preview",
            actor_role="admin",
            run_mode="interactive",
            environment="unknown",
            approval=None,
        ),
    )
    return dict(prepared.approval_view or {})


def ensure_read_tools(tools: Iterable[BaseTool]) -> None:
    invalid = [tool.name for tool in tools if tool.name not in READ_TOOLS_BY_NAME]
    if invalid:
        raise RuntimeError(f"以下工具未注册为读工具：{invalid}")


def ensure_write_tools(tools: Iterable[BaseTool]) -> None:
    invalid = [tool.name for tool in tools if tool.name not in WRITE_TOOLS_BY_NAME]
    if invalid:
        raise RuntimeError(f"以下工具未注册为写工具：{invalid}")
