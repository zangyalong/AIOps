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
from app.agents.tools.alerts import query_alerts
from app.agents.tools.commands import run_diagnostic_command
from app.agents.tools.correlate import correlate_incident
from app.agents.tools.deployment import deploy_server_environment
from app.agents.tools.grafana import grafana_explore_link
from app.agents.tools.logs import list_log_services, search_logs
from app.agents.tools.metrics import (
    list_metric_instances,
    query_metrics,
    query_server_metrics,
)
from app.agents.tools.server_log_config import (
    apply_server_log_config,
    preview_server_log_config,
)
from app.agents.tools.server_files import (
    find_server_files,
    preview_server_file_update,
    read_server_file,
    update_server_file,
)
from app.agents.tools.servers import query_servers
from app.agents.tools.service_discovery import (
    discover_server_services,
    register_server_services,
)
from app.agents.tools.service_topology import query_server_services

RiskLevel = Literal["medium", "high", "critical"]
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


READ_TOOLS: tuple[BaseTool, ...] = (
    query_servers,
    run_diagnostic_command,
    query_server_metrics,
    query_metrics,
    list_metric_instances,
    search_logs,
    list_log_services,
    query_alerts,
    grafana_explore_link,
    correlate_incident,
    query_server_services,
    discover_server_services,
    preview_server_log_config,
    find_server_files,
    read_server_file,
    preview_server_file_update,
)

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


def is_write_tool(tool_name: str) -> bool:
    return tool_name in WRITE_TOOL_SPECS


def describe_write_action(tool_call: dict[str, Any]) -> dict[str, Any]:
    name = str(tool_call.get("name") or "")
    spec = WRITE_TOOL_SPECS.get(name)
    if spec is None:
        raise ValueError(f"{name or '未知工具'} 不是已注册的写工具")
    return spec.pending_action(tool_call)


def ensure_read_tools(tools: Iterable[BaseTool]) -> None:
    invalid = [tool.name for tool in tools if tool.name not in READ_TOOLS_BY_NAME]
    if invalid:
        raise RuntimeError(f"以下工具未注册为读工具：{invalid}")


def ensure_write_tools(tools: Iterable[BaseTool]) -> None:
    invalid = [tool.name for tool in tools if tool.name not in WRITE_TOOLS_BY_NAME]
    if invalid:
        raise RuntimeError(f"以下工具未注册为写工具：{invalid}")
