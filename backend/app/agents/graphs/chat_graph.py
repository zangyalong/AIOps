from __future__ import annotations

import time
from functools import lru_cache, partial
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from app.agents.tools.actions import restart_service
from app.agents.tools.alerts import query_alerts
from app.agents.tools.changes import query_recent_changes
from app.agents.tools.catalog import (
    describe_write_action,
    ensure_read_tools,
    ensure_write_tools,
    is_write_tool,
)
from app.agents.tools.commands import run_diagnostic_command
from app.agents.tools.correlate import correlate_incident
from app.agents.tools.grafana import grafana_explore_link
from app.agents.tools.logs import list_log_services, search_logs
from app.agents.tools.metrics import (
    list_metric_instances,
    query_metrics,
    query_server_metrics,
)
from app.agents.tools.servers import query_servers
from app.core.config import settings

AgentName = Literal[
    "metrics_agent",
    "logs_agent",
    "incident_agent",
    "infrastructure_agent",
]
SupervisorRoute = Literal[
    "metrics_agent",
    "logs_agent",
    "incident_agent",
    "infrastructure_agent",
    "finish",
]

SPECIALIST_READ_TOOLS: dict[AgentName, list[BaseTool]] = {
    "metrics_agent": [
        query_server_metrics,
        query_metrics,
        list_metric_instances,
        grafana_explore_link,
    ],
    "logs_agent": [search_logs, list_log_services, grafana_explore_link],
    "incident_agent": [
        query_alerts,
        correlate_incident,
        query_recent_changes,
        query_servers,
    ],
    "infrastructure_agent": [query_servers, run_diagnostic_command],
}

SPECIALIST_WRITE_TOOLS: dict[AgentName, list[BaseTool]] = {
    "metrics_agent": [],
    "logs_agent": [],
    "incident_agent": [],
    "infrastructure_agent": [restart_service],
}

# 启动导入阶段就校验每个专家的工具归类，防止以后把写工具误塞进读工具列表绕过审批。
for _tools in SPECIALIST_READ_TOOLS.values():
    ensure_read_tools(_tools)
for _tools in SPECIALIST_WRITE_TOOLS.values():
    ensure_write_tools(_tools)

MAX_TOOL_ITERATIONS = 5
MAX_SUPERVISOR_ROUNDS = 4

SPECIALIST_PROMPTS: dict[AgentName, str] = {
    "metrics_agent": (
        "你是 AIOMS 的指标分析专家，只负责 Prometheus 指标和 Grafana 指标链接。"
        "用户给出已纳管服务器名称或 IP 时，必须优先一次调用 query_server_metrics，"
        "不要先调用 list_metric_instances。只有用户直接询问 Prometheus instance、全部实例或原始 "
        "PromQL 时才使用 query_metrics/list_metric_instances。用户明确要求图表或链接时才调用 "
        "grafana_explore_link，不要擅自增加图表任务。"
        "完成调查后输出一份给 Supervisor 的简短证据报告，不要假装直接回复最终用户。"
    ),
    "logs_agent": (
        "你是 AIOMS 的日志分析专家，只负责 Loki 日志和 Grafana 日志链接。"
        "需要当前真实日志时必须调用 search_logs；不知道服务名先调用 list_log_services。"
        "完成调查后输出一份给 Supervisor 的简短证据报告，不要假装直接回复最终用户。"
    ),
    "incident_agent": (
        "你是 AIOMS 的事件指挥专家，负责告警查询、事件关联，以及指标/日志/告警/变更的时间窗分析。"
        "优先用 correlate_incident 做同一实体、同一时间窗的关联；需要当前告警时调用 query_alerts，"
        "只需要单独核对发布、回滚、扩缩容或配置变更时调用 query_recent_changes。"
        "变更与故障时间重合只是相关性证据，不能在没有其他证据时直接断言为根因。"
        "完成调查后输出一份给 Supervisor 的简短证据报告，不要假装直接回复最终用户。"
    ),
    "infrastructure_agent": (
        "你是 AIOMS 的基础设施专家，负责查询纳管服务器、只读诊断和受控变更。"
        "当前服务器信息必须调用 query_servers；uptime/df/free 等只读信息调用 "
        "run_diagnostic_command，它不需要审批。只有用户明确要求重启服务时才能调用 "
        "restart_service；它是高风险写工具，必须等待人工审批，不能用诊断工具替代或拼接写命令。"
        "完成调查后输出一份给 Supervisor 的简短证据报告，不要假装直接回复最终用户。"
    ),
}

SUPERVISOR_PROMPT = """你是 AIOMS 的 Supervisor，只负责拆解问题和把任务交给最合适的专家，不亲自编造运维数据。

可委派的专家：
- metrics_agent：Prometheus 指标、实例发现、指标图表链接
- logs_agent：Loki 日志、服务发现、日志图表链接
- incident_agent：当前告警、变更记录、事件关联和跨信号横向诊断
- infrastructure_agent：服务器清单、SSH 只读诊断和需人工审批的受控服务重启

每次只选择一个专家。专家完成后你会再次拿到控制权，可以继续委派另一个专家；证据已经足够，或用户只是闲聊、解释概念时选择 finish。
涉及“当前/最新”的服务器状态、指标、日志、告警或命令输出时，不能在没有对应专家真实调查结果的情况下 finish。
不要重复委派已经回答清楚同一问题的专家。instruction 要写成该专家可以独立执行的具体任务，并带上必要实体、时间窗和筛选条件。
不要擅自增加用户没有要求的图表、链接或额外调查项，避免无意义的工具调用。
"""

SYNTHESIZER_PROMPT = """你是 AIOMS 面向用户的事件指挥官。根据 Supervisor 收集的专家报告和真实工具结果，给出清晰、简洁、可执行的最终回答。

铁律：
1. 服务器状态、命令输出、指标、日志和告警等具体数据只能引用本轮真实工具结果，禁止编造。
2. 历史工具结果只是旧快照；用户询问当前情况时只能使用本轮新证据。
3. 专家没有拿到数据时明确说明“当前拿不到”，不要补造一份看起来合理的结果。
4. 区分事实、推断和建议；只有本轮写工具结果明确证明执行成功时，才能声称操作已经执行，
   否则只能说明已批准、执行失败或给出建议。
"""


def _merge_reports(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """子图回到父图时按专家名合并报告；同一专家再次调查会用较新的报告覆盖旧报告。"""
    return {**left, **right}


class ChatState(TypedDict):
    # 对外可见并需要落库的消息：历史 + 本轮用户消息 + 真实工具轨迹 + 最终综合回答。
    messages: Annotated[list[BaseMessage], add_messages]
    # 当前轮的内部协作轨迹：Supervisor 交接、专家思考结果、工具调用；专家最终报告不直接展示。
    work_messages: Annotated[list[BaseMessage], add_messages]
    reports: Annotated[dict[str, str], _merge_reports]
    specialist_instruction: str
    tool_iterations: int
    supervisor_rounds: int
    # 聊天请求允许写工具进入审批门；无人值守 worker 必须传 False，从源头不把写工具挂给模型。
    allow_write_tools: bool
    approval: dict | None


class SpecialistState(TypedDict):
    """四个专家子图与父图共享的最小状态接口。"""

    messages: Annotated[list[BaseMessage], add_messages]
    work_messages: Annotated[list[BaseMessage], add_messages]
    reports: Annotated[dict[str, str], _merge_reports]
    specialist_instruction: str
    tool_iterations: int
    allow_write_tools: bool
    approval: dict | None


class SupervisorDecision(BaseModel):
    next_agent: SupervisorRoute = Field(description="下一位专家；证据足够时选择 finish")
    instruction: str = Field(
        description="给下一位专家的具体调查任务；选择 finish 时说明为什么可以结束"
    )


@lru_cache(maxsize=1)
def _base_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        # 多 Agent 一轮会产生多次模型调用，关闭思考模式控制延迟和 token 成本。
        extra_body={"thinking": {"type": "disabled"}},
    )


@lru_cache(maxsize=None)
def _specialist_llm(
    agent_name: AgentName, mode: Literal["none", "auto"], allow_write_tools: bool
) -> Runnable:
    if mode == "none":
        return _base_llm()
    tools = list(SPECIALIST_READ_TOOLS[agent_name])
    if allow_write_tools:
        tools.extend(SPECIALIST_WRITE_TOOLS[agent_name])
    return _base_llm().bind_tools(tools)


def _latest_human_content(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _conversation_transcript(messages: list[BaseMessage], limit: int = 12) -> str:
    """给 Supervisor/综合节点一份干净对话文本，跳过内部工具协议消息以免上下文膨胀。"""
    lines: list[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            lines.append(f"用户：{message.content}")
        elif isinstance(message, AIMessage) and not message.tool_calls:
            lines.append(f"助手：{message.content}")
    return "\n".join(lines[-limit:])


def _reports_text(reports: dict[str, str]) -> str:
    if not reports:
        return "（还没有专家报告）"
    return "\n\n".join(f"【{name}】\n{content}" for name, content in reports.items())


def _tool_results(work_messages: list[BaseMessage]) -> list[tuple[str, str]]:
    """提取当前轮所有专家拿到的真实工具结果，供最终综合引用。"""
    results: list[tuple[str, str]] = []
    for message in work_messages:
        if not isinstance(message, ToolMessage):
            continue
        content = str(message.content)
        if len(content) > 1200:
            content = content[:1200] + "…（已截断）"
        results.append((message.name or "（工具）", content))
    return results


def _evidence_text(work_messages: list[BaseMessage]) -> str:
    results = _tool_results(work_messages)
    if not results:
        return "（本轮没有调用任何工具）"
    return "\n\n".join(f"【{name}】返回：\n{content}" for name, content in results)


async def supervisor_node(
    state: ChatState,
) -> Command[
    Literal[
        "metrics_agent",
        "logs_agent",
        "incident_agent",
        "infrastructure_agent",
        "synthesize",
    ]
]:
    """父图协调者：看用户问题和已有报告，决定下一次 handoff 或进入最终综合。"""
    rounds = state.get("supervisor_rounds", 0)
    if rounds >= MAX_SUPERVISOR_ROUNDS:
        return Command(goto="synthesize")

    context = (
        f"对话上下文：\n{_conversation_transcript(state['messages'])}\n\n"
        f"本轮已有专家报告：\n{_reports_text(state.get('reports', {}))}"
    )

    t0 = time.monotonic()
    structured_llm = _base_llm().with_structured_output(
        SupervisorDecision, method="function_calling"
    )
    decision = await structured_llm.ainvoke(
        [SystemMessage(SUPERVISOR_PROMPT), HumanMessage(context)]
    )
    print(
        f"[graph] supervisor 耗时 {time.monotonic() - t0:.1f}s，"
        f"next={decision.next_agent}，instruction={decision.instruction!r}"
    )

    if decision.next_agent == "finish":
        return Command(goto="synthesize")

    question = _latest_human_content(state["messages"])
    instruction = decision.instruction.strip() or question
    handoff = HumanMessage(
        content=f"Supervisor 交接任务：{instruction}\n原始用户问题：{question}",
        name="supervisor",
    )
    return Command(
        goto=decision.next_agent,
        update={
            "work_messages": [handoff],
            "specialist_instruction": instruction,
            "tool_iterations": 0,
            "supervisor_rounds": rounds + 1,
            "approval": None,
        },
    )


async def specialist_node(agent_name: AgentName, state: SpecialistState) -> dict:
    """专家子图的 ReAct 节点：只绑定本领域工具，完成后把内部报告交回父图。"""
    t0 = time.monotonic()
    at_limit = state.get("tool_iterations", 0) >= MAX_TOOL_ITERATIONS
    allow_write = state.get("allow_write_tools", False)
    guidance = (
        "已达到本专家的工具调用上限，不能再调工具；请基于已有证据提交报告。"
        if at_limit
        else "需要真实数据就必须调用工具；历史内容不是当前证据，禁止猜测或编造。"
    )
    reports = _reports_text(state.get("reports", {}))
    system = (
        f"{SPECIALIST_PROMPTS[agent_name]}\n\n"
        f"本次任务：{state.get('specialist_instruction', '')}\n"
        f"其他专家已有报告：\n{reports}\n\n{guidance}"
    )
    mode: Literal["none", "auto"] = "none" if at_limit else "auto"
    response = await _specialist_llm(agent_name, mode, allow_write).ainvoke(
        [SystemMessage(system), *state.get("work_messages", [])]
    )
    response = response.model_copy(update={"name": agent_name})
    print(
        f"[graph] {agent_name} 耗时 {time.monotonic() - t0:.1f}s，"
        f"mode={mode}，tool_calls={response.tool_calls!r}"
    )

    if response.tool_calls:
        # 工具协议轨迹既放内部工作区，也进入 public messages，确保落库后仍是完整的
        # AIMessage(tool_calls) → ToolMessage 配对；专家最终报告则只留在内部，不冒充用户答案。
        return {"messages": [response], "work_messages": [response]}

    report = str(response.content).strip() or "（专家没有产出报告）"
    return {
        "work_messages": [response],
        "reports": {agent_name: report},
    }


async def specialist_tools_node(agent_name: AgentName, state: SpecialistState) -> dict:
    """执行当前专家刚提出的工具调用，并回到同一专家继续分析。"""
    t0 = time.monotonic()
    last = state["work_messages"][-1]
    assert isinstance(last, AIMessage)
    assigned_tools = [
        *SPECIALIST_READ_TOOLS[agent_name],
        *SPECIALIST_WRITE_TOOLS[agent_name],
    ]
    tool_map = {tool.name: tool for tool in assigned_tools}
    decision = state.get("approval")
    allow_write = state.get("allow_write_tools", False)
    results: list[BaseMessage] = []

    for tool_call in last.tool_calls:
        name = tool_call["name"]
        if name not in tool_map:
            result: BaseMessage = ToolMessage(
                content=f"{agent_name} 无权调用工具 {name}，已拒绝执行。",
                tool_call_id=tool_call["id"],
                name=name,
            )
        elif is_write_tool(name) and not allow_write:
            result = ToolMessage(
                content="无人值守任务禁止调用写工具，已拒绝执行。",
                tool_call_id=tool_call["id"],
                name=name,
            )
        elif is_write_tool(name) and decision is None:
            result = ToolMessage(
                content="写工具缺少人工审批决议，已拒绝执行。",
                tool_call_id=tool_call["id"],
                name=name,
            )
        elif is_write_tool(name) and not decision.get("approved", False):
            reason = (decision.get("reason") or "").strip() or "用户拒绝了本次操作"
            result = ToolMessage(
                content=f"用户拒绝执行该操作（{reason}）。请不要重试。",
                tool_call_id=tool_call["id"],
                name=name,
            )
        else:
            result = await tool_map[name].ainvoke(tool_call)
        results.append(result)

    print(f"[graph] {agent_name}.tools 耗时 {time.monotonic() - t0:.1f}s")
    return {
        "messages": results,
        "work_messages": results,
        "tool_iterations": state.get("tool_iterations", 0) + 1,
        "approval": None,
    }


async def approval_node(state: SpecialistState) -> dict:
    """基础设施专家子图中的人工审批门；interrupt 会由父图的 checkpointer 持久化。"""
    last = state["work_messages"][-1]
    assert isinstance(last, AIMessage)
    pending = [
        describe_write_action(tool_call)
        for tool_call in last.tool_calls
        if is_write_tool(tool_call["name"])
    ]
    decision = interrupt({"actions": pending})
    return {"approval": decision}


def route_after_specialist(state: SpecialistState) -> str:
    last = state["work_messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return END
    has_write = any(is_write_tool(call["name"]) for call in last.tool_calls)
    if has_write and state.get("allow_write_tools", False):
        return "approval"
    return "tools"


def build_specialist_subgraph(agent_name: AgentName) -> CompiledStateGraph:
    """每个专家都是独立 LangGraph 子图，而不是包成 Supervisor 的普通 tool。"""
    builder = StateGraph(SpecialistState)
    builder.add_node("specialist", partial(specialist_node, agent_name))
    builder.add_node("tools", partial(specialist_tools_node, agent_name))
    if SPECIALIST_WRITE_TOOLS[agent_name]:
        builder.add_node("approval", approval_node)
        builder.add_edge("approval", "tools")

    builder.add_edge(START, "specialist")
    builder.add_conditional_edges("specialist", route_after_specialist)
    builder.add_edge("tools", "specialist")
    # 不给子图自己的 checkpointer：每次 handoff 独立运行，同时继承父图 checkpointer 以支持 interrupt。
    return builder.compile()


async def synthesize_node(state: ChatState) -> dict:
    """收束节点：只调用一次模型，用真实证据生成面向用户的最终回答。"""
    context = (
        f"对话上下文：\n{_conversation_transcript(state['messages'])}\n\n"
        f"专家报告：\n{_reports_text(state.get('reports', {}))}\n\n"
        f"本轮真实工具结果：\n{_evidence_text(state.get('work_messages', []))}"
    )

    t0 = time.monotonic()
    response = await _base_llm().ainvoke(
        [SystemMessage(SYNTHESIZER_PROMPT), HumanMessage(context)]
    )
    print(f"[graph] synthesize 耗时 {time.monotonic() - t0:.1f}s")
    return {"messages": [response]}


def build_chat_graph() -> CompiledStateGraph:
    """构建父级 Supervisor 图，并把四个共享状态的专家子图作为真正节点接入。"""
    builder = StateGraph(ChatState)
    builder.add_node("supervisor", supervisor_node)
    for agent_name in SPECIALIST_READ_TOOLS:
        builder.add_node(agent_name, build_specialist_subgraph(agent_name))
        builder.add_edge(agent_name, "supervisor")
    builder.add_node("synthesize", synthesize_node)

    builder.add_edge(START, "supervisor")
    builder.add_edge("synthesize", END)

    # 父图 checkpointer 同时承接聊天审批 interrupt；每轮仍由 chat_service 使用独立 thread_id。
    return builder.compile(checkpointer=MemorySaver())
