from __future__ import annotations

import json
import time
import uuid
from functools import cache, lru_cache
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.agents.presentation import compact_narration, restore_truncated_tool_links
from app.agents.prompts import (
    DefaultServerContext,
    LongTermMemoryContext,
    build_ops_system_prompt,
)
from app.agents.skills import expand_explicit_skill_invocation
from app.agents.tools.catalog import (
    READ_TOOLS_BY_NAME,
    WRITE_TOOLS_BY_NAME,
    build_scoped_operation_broker,
    ensure_read_tools,
    ensure_write_tools,
    get_operation_spec,
    is_write_tool,
)
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.operations.schemas import (
    ApprovalDecision,
    OperationCall,
    OperationContext,
    OperationResult,
)
from app.services import operation_record_service

OPS_READ_TOOLS: tuple[BaseTool, ...] = tuple(READ_TOOLS_BY_NAME.values())
OPS_WRITE_TOOLS: tuple[BaseTool, ...] = tuple(WRITE_TOOLS_BY_NAME.values())

# 启动导入时即验证读写边界，防止以后把写工具误挂到自动允许的调查能力中。
ensure_read_tools(OPS_READ_TOOLS)
ensure_write_tools(OPS_WRITE_TOOLS)

GRAPH_RECURSION_LIMIT = 10_000


class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    # 交互聊天允许模型提出结构化写操作；无人值守 worker 必须传 False。
    allow_write_tools: bool
    approval: dict | None
    # 每次图执行都有独立审计 ID；只有交互式会话同时绑定 chat_run。
    operation_execution_id: str | None
    operation_chat_run_id: str | None
    operation_context: dict


def graph_run_config(thread_id: str) -> RunnableConfig:
    """构造聊天与 worker 共用的图配置，避免 LangGraph 默认 25 步误停长任务。"""
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": GRAPH_RECURSION_LIMIT,
    }


@lru_cache(maxsize=1)
def _base_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        extra_body={"thinking": {"type": "disabled"}},
    )


@cache
def _ops_llm(allow_write_tools: bool) -> Runnable:
    return _base_llm().bind_tools(_ops_tools(allow_write_tools))


def _ops_tools(allow_write_tools: bool) -> tuple[BaseTool, ...]:
    if allow_write_tools:
        return (*OPS_READ_TOOLS, *OPS_WRITE_TOOLS)
    return OPS_READ_TOOLS


def _normalize_tool_call_ids(message: AIMessage) -> AIMessage:
    """确保同一模型消息中的工具调用 ID 非空且唯一。"""
    seen: set[str] = set()
    normalized: list[dict] = []
    changed = False
    for call in message.tool_calls:
        item = dict(call)
        call_id = str(item.get("id") or "")
        if not call_id or call_id in seen:
            call_id = f"call_aiops_{uuid.uuid4().hex}"
            item["id"] = call_id
            changed = True
        seen.add(call_id)
        normalized.append(item)
    if not changed:
        return message
    return message.model_copy(update={"tool_calls": normalized})


def _tool_call_signature(tool_call: dict) -> tuple[str, str]:
    args = tool_call.get("args") or {}
    return (
        str(tool_call.get("name") or ""),
        json.dumps(
            args,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )


def _failed_invalid_investigation_signatures(
    messages: list[BaseMessage],
) -> set[tuple[str, str]]:
    """收集已经得到参数协议错误的调查调用，供确定性防重使用。"""
    pending: dict[str, tuple[str, str]] = {}
    failed: set[tuple[str, str]] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call.get("name") == "investigate":
                    pending[str(call.get("id") or "")] = _tool_call_signature(call)
            continue
        if not isinstance(message, ToolMessage) or message.name != "investigate":
            continue
        signature = pending.pop(message.tool_call_id, None)
        content = message.content if isinstance(message.content, str) else ""
        if (
            signature is not None
            and message.status == "error"
            and content.startswith("调查请求不完整：")
        ):
            failed.add(signature)
    return failed


def _stop_repeated_invalid_investigation(
    response: AIMessage,
    history: list[BaseMessage],
) -> AIMessage:
    """模型忽略纠错提示时，阻止完全相同的无效调查再次进入工具节点。"""
    if not response.tool_calls:
        return response
    failed = _failed_invalid_investigation_signatures(history)
    if not failed or not all(
        call.get("name") == "investigate" and _tool_call_signature(call) in failed
        for call in response.tool_calls
    ):
        return response
    return AIMessage(
        content=(
            "同一调查请求已经因参数协议错误失败，系统已停止重复执行。"
            "请按上一条工具错误给出的正确结构重新提问；若没有示例，请补充明确的调查目标。"
        ),
        name="ops_agent",
    )


def _trailing_protocol_repairs(messages: list[BaseMessage]) -> list[ToolMessage]:
    """为尾部未响应的 tool_calls 补失败结果，保证下一次模型请求协议完整。"""
    pending: dict[str, str] = {}
    for message in messages:
        if pending:
            if isinstance(message, ToolMessage):
                pending.pop(message.tool_call_id, None)
                continue
            # 缺口已经不在尾部，简单追加无法恢复紧邻关系。
            return []
        if isinstance(message, AIMessage) and message.tool_calls:
            pending = {
                str(call.get("id") or ""): str(call.get("name") or "未知工具")
                for call in message.tool_calls
            }

    return [
        ToolMessage(
            content=(
                f"工具 {name} 没有产生可用结果，系统已按执行失败处理。"
                "不要假定操作成功，也不要自动重试写操作。"
            ),
            tool_call_id=call_id,
            name=name,
            status="error",
        )
        for call_id, name in pending.items()
    ]


def _current_turn_tool_outputs(messages: list[BaseMessage]) -> tuple[str, ...]:
    """只取最后一条用户消息之后的工具结果，避免旧轮链接参与恢复。"""
    last_human = max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage)
        ),
        default=-1,
    )
    return tuple(
        message.content
        for message in messages[last_human + 1 :]
        if isinstance(message, ToolMessage) and isinstance(message.content, str)
    )


async def ops_agent_node(state: ChatState) -> dict:
    """单一 ReAct 节点：选择调查/变更工具，或直接给出最终回答。"""
    started_at = time.monotonic()
    protocol_repairs = _trailing_protocol_repairs(state.get("messages", []))
    allow_write_tools = state.get("allow_write_tools", False)
    operation_context = state.get("operation_context") or {}
    raw_default_server = operation_context.get("default_server")
    default_server = (
        DefaultServerContext(
            id=str(raw_default_server.get("id") or ""),
            name=str(raw_default_server.get("name") or ""),
            ip_address=str(raw_default_server.get("ip_address") or ""),
        )
        if isinstance(raw_default_server, dict)
        and raw_default_server.get("id")
        and raw_default_server.get("name")
        and raw_default_server.get("ip_address")
        else None
    )
    raw_memories = operation_context.get("long_term_memories")
    memory_items = raw_memories if isinstance(raw_memories, list) else []
    long_term_memories = tuple(
        LongTermMemoryContext(
            category=str(item.get("category") or ""),
            content=str(item.get("content") or ""),
            updated_at=str(item.get("updated_at") or ""),
        )
        for item in memory_items
        if isinstance(item, dict)
    )
    assembly = build_ops_system_prompt(
        tools=_ops_tools(allow_write_tools),
        allow_write_tools=allow_write_tools,
        run_mode=str(operation_context.get("run_mode") or ""),
        default_server=default_server,
        long_term_memories=long_term_memories,
    )
    model_messages, explicit_skill, invocation_error = expand_explicit_skill_invocation(
        [*state["messages"], *protocol_repairs],
        assembly.installed_skills,
    )
    system_prompt = assembly.text
    if invocation_error is not None:
        system_prompt = (
            f"{system_prompt}\n\n# 显式 Skill 调用\n{invocation_error}\n"
            "不要假定该 Skill 已加载；直接向用户说明可用名称。"
        )
    response = await _ops_llm(allow_write_tools).ainvoke(
        [
            SystemMessage(system_prompt),
            *model_messages,
        ]
    )
    response = _normalize_tool_call_ids(
        response.model_copy(update={"name": "ops_agent"})
    )
    response = _stop_repeated_invalid_investigation(response, model_messages)
    if response.tool_calls:
        raw_narration = response.content if isinstance(response.content, str) else ""
        response = response.model_copy(
            update={"content": compact_narration(raw_narration)}
        )
    elif isinstance(response.content, str):
        response = response.model_copy(
            update={
                "content": restore_truncated_tool_links(
                    response.content,
                    _current_turn_tool_outputs(model_messages),
                )
            }
        )
    print(
        f"[graph] ops_agent 耗时 {time.monotonic() - started_at:.1f}s，"
        f"explicit_skill={explicit_skill.name if explicit_skill else None!r}，"
        f"tool_calls={response.tool_calls!r}"
    )
    return {"messages": [*protocol_repairs, response]}


def _tool_message(
    *,
    tool_call: dict,
    content: object,
    status: Literal["success", "error"] = "success",
) -> ToolMessage:
    return ToolMessage(
        content=content if isinstance(content, (str, list)) else str(content),
        tool_call_id=str(tool_call.get("id") or ""),
        name=str(tool_call.get("name") or "未知工具"),
        status=status,
    )


def _operation_context_from_state(
    state: ChatState,
    approval: ApprovalDecision | None,
) -> OperationContext:
    return OperationContext.from_mapping(
        state.get("operation_context"), approval=approval
    )


def _approval_for_tool_call(
    raw_approval: dict | None,
    tool_call_id: str,
) -> ApprovalDecision | None:
    """只取当前冻结操作自己的裁决，禁止批次级批准。"""
    if raw_approval is None:
        return None
    decisions = raw_approval.get("decisions")
    if isinstance(decisions, dict):
        value = decisions.get(tool_call_id)
        return ApprovalDecision.from_mapping(value) if isinstance(value, dict) else None
    return None


def _assigned_tools(allow_write_tools: bool) -> list[BaseTool]:
    tools = list(OPS_READ_TOOLS)
    if allow_write_tools:
        tools.extend(OPS_WRITE_TOOLS)
    return tools


async def tools_node(state: ChatState) -> dict:
    """通过统一 Operation Broker 执行调查或已审批变更。"""
    started_at = time.monotonic()
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    assigned_tools = _assigned_tools(state.get("allow_write_tools", False))
    tool_map = {tool.name: tool for tool in assigned_tools}
    operation_broker = build_scoped_operation_broker(assigned_tools)
    raw_approval = state.get("approval")
    results: list[BaseMessage] = []
    raw_execution_id = state.get("operation_execution_id")
    execution_id = uuid.UUID(raw_execution_id) if raw_execution_id else None
    raw_chat_run_id = state.get("operation_chat_run_id")
    chat_run_id = uuid.UUID(raw_chat_run_id) if raw_chat_run_id else None

    for tool_call in last.tool_calls:
        name = tool_call["name"]
        audit_started = False
        audit_metadata: dict[str, object] | None = None
        if name not in tool_map:
            result: BaseMessage = _tool_message(
                tool_call=tool_call,
                content=f"当前运行模式无权调用工具 {name}，已拒绝执行。",
                status="error",
            )
            results.append(result)
            continue

        try:
            operation_call = OperationCall.from_tool_call(tool_call)
            approval = _approval_for_tool_call(
                raw_approval,
                str(tool_call.get("id") or ""),
            )
            operation_context = _operation_context_from_state(state, approval)
            spec = get_operation_spec(name)
            if (
                spec is not None
                and spec.effect == "write"
                and (execution_id is None or chat_run_id is None)
            ):
                result = _tool_message(
                    tool_call=tool_call,
                    content="写操作缺少持久化聊天运行标识，已按安全策略拒绝执行。",
                    status="error",
                )
                results.append(result)
                continue
            prepared = await operation_broker.aprepare(
                operation_call, operation_context
            )

            if (
                prepared.spec.effect == "write"
                and execution_id is not None
                and chat_run_id is not None
            ):
                async with AsyncSessionLocal() as db:
                    await operation_record_service.ensure_pending(
                        db,
                        execution_id=execution_id,
                        chat_run_id=chat_run_id,
                        prepared=prepared,
                    )
                    record = await operation_record_service.authorize_execution(
                        db,
                        execution_id=execution_id,
                        prepared=prepared,
                        approval=approval,
                    )
                audit_started = record.status == "executing"
            elif prepared.spec.audit_execution:
                if execution_id is None:
                    result = _tool_message(
                        tool_call=tool_call,
                        content="调查缺少持久化审计标识，已按安全策略拒绝执行。",
                        status="error",
                    )
                    results.append(result)
                    continue
                async with AsyncSessionLocal() as db:
                    record = await operation_record_service.begin_read_execution(
                        db,
                        execution_id=execution_id,
                        chat_run_id=chat_run_id,
                        prepared=prepared,
                    )
                audit_started = record.status == "executing"

            if prepared.decision.result != "allow":
                result = _tool_message(
                    tool_call=tool_call,
                    content=prepared.decision.reason,
                    status="error",
                )
                results.append(result)
                continue

            raw_result = await operation_broker.execute_prepared(prepared)
            if isinstance(raw_result, OperationResult):
                content = raw_result.message
                status = "success" if raw_result.ok else "error"
                audit_metadata = (
                    dict(raw_result.metadata)
                    if raw_result.metadata is not None
                    else None
                )
            elif isinstance(raw_result, ToolMessage):
                content = raw_result.content
                status = raw_result.status
            else:
                content = raw_result
                status = "success"
            result = _tool_message(
                tool_call=tool_call,
                content=content,
                status=status,
            )
            if audit_started and execution_id is not None:
                async with AsyncSessionLocal() as db:
                    await operation_record_service.finish_execution(
                        db,
                        execution_id=execution_id,
                        tool_call_id=operation_call.id,
                        result=content if status == "success" else None,
                        error=str(content) if status == "error" else None,
                        metadata=audit_metadata,
                    )
        except Exception as exc:  # noqa: BLE001 - 每个 call 都必须返回协议消息
            if audit_started and execution_id is not None:
                try:
                    async with AsyncSessionLocal() as db:
                        await operation_record_service.finish_execution(
                            db,
                            execution_id=execution_id,
                            tool_call_id=str(tool_call.get("id") or ""),
                            error=f"{type(exc).__name__}: {exc}",
                        )
                except Exception as audit_exc:  # noqa: BLE001
                    print(f"[operation-audit] 记录执行失败时出错：{audit_exc}")
            result = _tool_message(
                tool_call=tool_call,
                content=f"工具 {name} 执行失败：{type(exc).__name__}: {exc}",
                status="error",
            )
        results.append(result)

    print(f"[graph] tools 耗时 {time.monotonic() - started_at:.1f}s")
    return {
        "messages": results,
        "approval": None,
    }


async def approval_node(state: ChatState) -> dict:
    """统一人工审批门；interrupt 由父图 checkpointer 持久化。"""
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    context = _operation_context_from_state(state, None)
    operation_broker = build_scoped_operation_broker(
        _assigned_tools(state.get("allow_write_tools", False))
    )
    pending: list[dict] = []
    raw_execution_id = state.get("operation_execution_id")
    execution_id = uuid.UUID(raw_execution_id) if raw_execution_id else None
    raw_chat_run_id = state.get("operation_chat_run_id")
    chat_run_id = uuid.UUID(raw_chat_run_id) if raw_chat_run_id else None
    for tool_call in last.tool_calls:
        if not is_write_tool(tool_call["name"]):
            continue
        prepared = await operation_broker.aprepare(
            OperationCall.from_tool_call(tool_call),
            context,
        )
        if prepared.approval_view is None:
            continue
        approval_view = dict(prepared.approval_view)
        if execution_id is not None and chat_run_id is not None:
            async with AsyncSessionLocal() as db:
                record = await operation_record_service.ensure_pending(
                    db,
                    execution_id=execution_id,
                    chat_run_id=chat_run_id,
                    prepared=prepared,
                )
            approval_view.update(
                {
                    "operation_id": str(record.id),
                    "request_hash": record.request_hash,
                    "expires_at": record.expires_at.isoformat(),
                }
            )
        pending.append(approval_view)
    decision = interrupt({"actions": pending})
    return {"approval": decision}


async def route_after_agent(
    state: ChatState,
) -> Literal["approval", "tools", "__end__"]:
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return END
    context = _operation_context_from_state(state, None)
    operation_broker = build_scoped_operation_broker(
        _assigned_tools(state.get("allow_write_tools", False))
    )
    for tool_call in last.tool_calls:
        if get_operation_spec(tool_call["name"]) is None:
            continue
        try:
            prepared = await operation_broker.aprepare(
                OperationCall.from_tool_call(tool_call),
                context,
            )
        except (TypeError, ValueError):
            # 参数不合法时交给 tools_node 生成与 tool_call_id 配对的失败消息。
            return "tools"
        if prepared.decision.result == "approval_required":
            return "approval"
    return "tools"


def build_chat_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """构建统一 Ops Agent 图：调查自动执行，变更进入同一个审批门。"""
    builder = StateGraph(ChatState)
    builder.add_node("ops_agent", ops_agent_node)
    builder.add_node("tools", tools_node)
    builder.add_node("approval", approval_node)

    builder.add_edge(START, "ops_agent")
    builder.add_conditional_edges("ops_agent", route_after_agent)
    builder.add_edge("tools", "ops_agent")
    builder.add_edge("approval", "tools")

    return builder.compile(checkpointer=checkpointer or MemorySaver())
