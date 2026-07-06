from __future__ import annotations

import time
from functools import lru_cache
from operator import add
from typing import Annotated, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from app.agents.tools.alerts import query_alerts
from app.agents.tools.commands import run_whitelisted_command
from app.agents.tools.logs import search_logs
from app.agents.tools.servers import query_servers
from app.core.config import settings

TOOLS = [query_servers, search_logs, query_alerts, run_whitelisted_command]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

# 单次尝试内 agent <-> tools 循环最多走几轮，防止模型死循环调工具；到上限后 agent_node
# 会直接不给模型挂工具（mode="none"），从 API 层面逼它基于已有信息收尾。
MAX_TOOL_ITERATIONS = 5

# Reflexion：evaluate 最多质检几次。到上限还没通过就把当前答案作为最终答案收尾，
# 避免"永远不满意"死循环。2 表示：初版答案 + 最多 1 次带反思的重试。
MAX_REVIEW_ATTEMPTS = 2

PERSONA = (
    "你是 AIOMS 的运维助手。可用工具：\n"
    "- query_servers：查询 servers 表里的服务器列表\n"
    "- search_logs：模拟日志检索（还没接入真实日志系统，结果是模拟数据）\n"
    "- query_alerts：查询 alert_events 表里未处理的告警（表里数据是手动模拟生成的，还没接真实监控系统）\n"
    "- run_whitelisted_command：通过 SSH 在白名单内执行只读诊断命令（只对配置了 SSH 连接信息的服务器生效，是真实执行）\n"
    "\n"
    "【铁律】涉及服务器状态、命令输出、指标、日志、告警等任何具体数据，只能通过实际调用上面的"
    "工具获取，绝对禁止编造、猜测，或拿历史对话里出现过的数据当成本轮的真实结果。需要数据就去"
    "调用对应工具；工具没返回、或服务器没配置无法执行的，就如实说“拿不到/需要调用工具才能知道”，"
    "不要自己编一份看起来真实的数据。工具返回的模拟数据也要如实说明是模拟的，不要当真实结果陈述。"
)


class ReflexionVerdict(BaseModel):
    passed: bool = Field(description="回答是否已经正确、完整地回应了用户这一轮的问题")
    reflection: str = Field(
        description="不通过时给出一句可操作的改进建议（告诉助手下次具体该怎么做）；通过时留空字符串"
    )


class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    # 此前对话历史压成的一段背景摘要（不是一条条真 AIMessage，避免"没调工具直接吐数据"的
    # 坏样例污染上下文）；由 chat_service 传入，agent 每步注入系统提示。见 _build_history_summary。
    history_summary: str
    # 单次尝试内 agent <-> tools 来回了几轮，用来卡 MAX_TOOL_ITERATIONS；每次重试会重置为 0
    tool_iterations: int
    # 累积的自我反思，evaluate 不通过时追加（add 归约器 = 往后拼），agent 每次带着它改进
    reflections: Annotated[list[str], add]
    # evaluate 质检了几轮，用来卡 MAX_REVIEW_ATTEMPTS
    review_attempts: int
    # 最近一次 evaluate 是否通过，给路由用
    passed: bool
    # 质检打回时若发现"该调工具却没调"，置 True，逼重试的下一步必须调工具（agent 消费后复位）
    force_tools: bool


@lru_cache(maxsize=None)
def _llm(mode: str) -> ChatOpenAI:
    """mode: "none"（不挂工具）| "auto"（挂工具，调不调自己定）| "required"（挂工具且必须至少调一个）。"""
    llm = ChatOpenAI(
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        # 关掉思考模式：思考过程要多生成一截 token，而 ReAct+Reflexion 一轮对话可能串行跑
        # 好几次 LLM 调用（每次调工具、每次质检、每次重试都要再调一次），思考模式明显拖慢响应。
        extra_body={"thinking": {"type": "disabled"}},
    )
    if mode == "none":
        return llm
    if mode == "required":
        return llm.bind_tools(TOOLS, tool_choice="required")
    return llm.bind_tools(TOOLS)


def _latest_human_content(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _tools_used_since_last_human(messages: list[BaseMessage]) -> list[str]:
    """本轮（最近一条用户消息之后）实际发起过哪些工具调用，给 evaluate 判断有没有编造用。"""
    used: list[str] = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage) and message.tool_calls:
            used.extend(tc["name"] for tc in message.tool_calls)
    return list(reversed(used))


async def agent_node(state: ChatState) -> dict:
    """ReAct 的核心节点：挂着工具的模型看当前对话，自己决定调工具还是直接给最终答案。

    如果上一轮质检打回并留了反思，会把反思注入 prompt，让模型带着教训重做。
    到工具调用步数上限后，不再把工具挂给模型（mode="none"），从 API 层面逼它收尾。
    """
    t0 = time.monotonic()
    at_limit = state.get("tool_iterations", 0) >= MAX_TOOL_ITERATIONS
    # 只在"质检点名该调工具却没调"的重试首步（tool_iterations 已被重置为 0）强制调工具；
    # 到上限时不能强制（那时本就不该再挂工具）。force_tools 用完即在返回值里复位，只作用一步，
    # 否则会强制→回 agent→仍强制→…死循环撞满 MAX_TOOL_ITERATIONS。
    use_required = (
        state.get("force_tools", False)
        and not at_limit
        and state.get("tool_iterations", 0) == 0
    )
    if at_limit:
        guidance = "已达到工具调用次数上限，不能再调用任何工具，请直接基于目前已知的信息回答。"
    else:
        guidance = (
            "需要真实数据（服务器状态、命令结果、日志、告警）就现在调用对应工具去拿，"
            "不要凭空回答、也不要拿历史对话里的数据当成本轮的真实结果。"
        )
    reflections = state.get("reflections", [])
    if reflections:
        joined = "\n".join(f"- {r}" for r in reflections)
        guidance += f"\n你之前回答本轮问题时的自我反思，这次务必据此改进：\n{joined}"
    # 历史压成的背景摘要放进同一条 SystemMessage（而不是当成一条条历史消息进 messages），
    # 避免坏样例污染，也避免出现两条并排的 SystemMessage。
    system_parts = [PERSONA]
    history_summary = state.get("history_summary", "")
    if history_summary:
        system_parts.append(history_summary)
    system_parts.append(guidance)
    prompt = [SystemMessage("\n\n".join(system_parts)), *state["messages"]]
    mode = "none" if at_limit else ("required" if use_required else "auto")
    response = await _llm(mode).ainvoke(prompt)
    print(
        f"[graph] agent_node 耗时 {time.monotonic() - t0:.1f}s，mode={mode}，"
        f"tool_calls={response.tool_calls!r}"
    )
    return {"messages": [response], "force_tools": False}


async def tools_node(state: ChatState) -> dict:
    """执行上一条 AI 消息里的所有 tool_calls，把结果作为 ToolMessage 追加回消息列表。"""
    t0 = time.monotonic()
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    results = []
    for tool_call in last.tool_calls:
        selected_tool = TOOLS_BY_NAME[tool_call["name"]]
        result = await selected_tool.ainvoke(tool_call["args"])
        results.append(ToolMessage(content=result, tool_call_id=tool_call["id"]))
    print(f"[graph] tools_node 耗时 {time.monotonic() - t0:.1f}s")
    return {"messages": results, "tool_iterations": state.get("tool_iterations", 0) + 1}


async def evaluate_node(state: ChatState) -> dict:
    """Reflexion 的评估+反思：质检 agent 的候选答案，不通过就产出一条可操作的自我反思。

    用结构化输出（method="function_calling"）拿判断——DeepSeek 不支持 with_structured_output
    默认的 response_format=json_schema 严格模式（400 unavailable），走 tool-calling 机制。
    """
    t0 = time.monotonic()
    question = _latest_human_content(state["messages"])
    answer = str(state["messages"][-1].content)
    tools_used = _tools_used_since_last_human(state["messages"])
    prompt = [
        SystemMessage(
            "你是质检员，判断助手的回答是否正确、完整地回应了用户这一轮的问题。评判要点：\n"
            "1. 是否回应了用户问的所有点，没有遗漏；\n"
            "2. 回答里若陈述了服务器状态/命令输出/指标/日志/告警等具体数据，本轮必须实际调用过"
            "对应工具才可信——没调相应工具却给出这类数据，判为不通过（很可能是编造的）；\n"
            "3. 明显答非所问、空泛或自相矛盾，判为不通过。\n"
            "不通过时给出一句可操作的改进建议（reflection），告诉助手下次具体该怎么做；通过时 reflection 留空。"
        ),
        HumanMessage(
            f"用户问题：{question}\n"
            f"本轮实际调用过的工具：{tools_used or '（没有调用任何工具）'}\n"
            f"助手回答：{answer}"
        ),
    ]
    structured_llm = _llm("none").with_structured_output(
        ReflexionVerdict, method="function_calling"
    )
    verdict = await structured_llm.ainvoke(prompt)
    review_attempts = state.get("review_attempts", 0) + 1
    print(
        f"[graph] evaluate_node 耗时 {time.monotonic() - t0:.1f}s，"
        f"passed={verdict.passed}，reflection={verdict.reflection!r}"
    )
    if verdict.passed:
        return {"passed": True, "review_attempts": review_attempts}

    # 打回重试：把本轮产生的草稿（含 tool_calls / ToolMessage）整批从 messages 里删掉，
    # 只留"历史 + 本轮用户问题"，避免重试的 agent 被自己刚才的错误答案锚定越纠越偏；
    # 唯一的纠偏信号是注入的 reflection。必须删"最近一条用户消息之后的全部"，把成对的
    # tool_call+ToolMessage 一起带走，别留半截（否则 DeepSeek 工具校验可能 400）。
    messages = state["messages"]
    last_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1
    )
    to_remove = [RemoveMessage(id=m.id) for m in messages[last_human_idx + 1 :]]
    return {
        "passed": False,
        "review_attempts": review_attempts,
        "reflections": [verdict.reflection],
        "tool_iterations": 0,  # 重试是一次全新尝试，重新给满工具预算
        "messages": to_remove,
        # 这轮"该给数据却没调工具"→逼重试首步必须调工具（详见 agent_node 的 use_required）
        "force_tools": not tools_used,
    }


def route_after_agent(state: ChatState) -> str:
    """agent 吐的消息带 tool_calls 就去执行工具，否则这条是候选答案，交给 evaluate 质检。"""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "evaluate"


def route_after_evaluate(state: ChatState) -> str:
    """通过、或到了质检次数上限就结束；否则带着反思回到 agent 重做。"""
    if state.get("passed") or state.get("review_attempts", 0) >= MAX_REVIEW_ATTEMPTS:
        return END
    return "agent"


def build_chat_graph() -> CompiledStateGraph:
    # ReAct + Reflexion：agent ⇄ tools 跑出候选答案，evaluate 质检；不通过就带着自我反思
    # 回 agent 重做，直到通过或到质检上限。
    # 不传 checkpointer：多轮对话的持久化自己用 messages 表管（每轮把完整历史当输入喂进来），
    # 图只在一次 astream_events 调用内部跑，这些环不需要 checkpointer。
    builder = StateGraph(ChatState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.add_node("evaluate", evaluate_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent)
    builder.add_edge("tools", "agent")
    builder.add_conditional_edges("evaluate", route_after_evaluate)

    return builder.compile()
