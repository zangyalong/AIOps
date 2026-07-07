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
)
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from app.agents.tools.alerts import query_alerts
from app.agents.tools.commands import run_whitelisted_command
from app.agents.tools.logs import list_log_services, search_logs
from app.agents.tools.metrics import list_metric_instances, query_metrics
from app.agents.tools.servers import query_servers
from app.core.config import settings

# query_alerts 还没接（任务3 接 Alertmanager 时启用）
TOOLS = [
    query_servers,
    run_whitelisted_command,
    query_metrics,
    list_metric_instances,
    search_logs,
    list_log_services,
]
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
    "- query_metrics：查询服务器实时监控指标（CPU/内存/磁盘使用率、目标存活），数据来自 Prometheus\n"
    "- list_metric_instances：列出 Prometheus 里有哪些实例的指标可查。不确定 instance 名时，先调它看有哪些，再调 query_metrics\n"
    "- search_logs：查真实日志，数据来自 Loki（可按服务名 service、关键词 keyword、级别 level、时间窗 minutes 过滤）\n"
    "- list_log_services：列出 Loki 里有哪些服务的日志可查。不确定 service 名时，先调它看有哪些，再调 search_logs\n"
    "- query_alerts：查询 alert_events 表里未处理的告警（表里数据是手动模拟生成的，还没接真实监控系统）\n"
    "- run_whitelisted_command：通过 SSH 在白名单内执行只读诊断命令（只对配置了 SSH 连接信息的服务器生效，是真实执行）\n"
    "\n"
    "【铁律】涉及服务器状态、命令输出、指标、日志、告警等任何具体数据，只能通过实际调用上面的"
    "工具获取，绝对禁止编造或猜测。**上文历史消息里那些工具返回的结果，都是过去某个时刻的旧快照，"
    "不代表现在。**\n"
    "调用工具前先用一句话说明调用的目的和理由，调用后再用一句话说明结果所代表的含义。\n"
    "只要用户问的是某台服务器“此刻”的属性或状态——IP、名称、运行时间、负载、磁盘、是否在线等——"
    "哪怕上文历史里已经出现过这个值、哪怕它看起来“一般不会变”（比如 IP 地址），也必须在本轮先重新"
    "调用对应工具拿到最新值再回答，绝对不能直接拿历史里的旧值抢答。不要用“这个值通常不变”给自己"
    "找借口——你无从判断它有没有变，唯一可靠的做法就是现在调工具重新查。判断很简单：用户想知道某台"
    "服务器现在真实是什么样 → 先调工具。\n"
    "唯一例外：用户明确是在“回顾历史本身”（如“刚才那个命令返回了什么”“我最开始问的是什么”“上面"
    "第一次查的结果是啥”），这是在问对话记录、不是问服务器现状，可以直接引用上文、不必重新调工具。\n"
    "工具没返回、或服务器没配置 SSH 无法执行的，就如实说“拿不到/需要调用工具才能知道”，不要自己编"
    "一份看起来真实的数据。工具返回的模拟数据也要如实说明是模拟的，不要当真实结果陈述。"
)


class ReflexionVerdict(BaseModel):
    passed: bool = Field(description="回答是否已经正确、完整地回应了用户这一轮的问题")
    reflection: str = Field(
        description="不通过时给出一句可操作的改进建议（告诉助手下次具体该怎么做）；通过时留空字符串"
    )


class ChatState(TypedDict):
    # 历史以完整真轨迹的形式直接在 messages 里（chat_service 加载后 prepend 在本轮问题前），
    # 不再单独走一个背景摘要字段。
    messages: Annotated[list[BaseMessage], add_messages]
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
    # 历史已经以真消息形式在 state["messages"] 里了，这里 SystemMessage 只放 PERSONA + guidance。
    prompt = [SystemMessage(f"{PERSONA}\n{guidance}"), *state["messages"]]
    mode = "none" if at_limit else ("required" if use_required else "auto")
    response = await _llm(mode).ainvoke(prompt)
    print(
        f"[graph] agent_node 耗时 {time.monotonic() - t0:.1f}s，mode={mode}，"
        f"tool_calls={response.tool_calls!r}"
    )
    return {"messages": [response], "force_tools": False}


async def tools_node(state: ChatState) -> dict:
    """执行上一条 AI 消息里的所有 tool_calls。传完整 tool_call 给工具（而不是只传 args），
    工具直接返回带 tool_call_id 的 ToolMessage——这样 tool_call_id 自动对上，落库/回放都干净。"""
    t0 = time.monotonic()
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    results = [
        await TOOLS_BY_NAME[tc["name"]].ainvoke(tc) for tc in last.tool_calls
    ]
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
            "2. 关于回答里的“具体数据”（服务器状态/命令输出/指标/日志/告警等），先分清用户这轮想要哪种：\n"
            "   (a) 想要【当前/最新的真实值，或某集合此刻有没有变化】——如“现在磁盘占用多少”“服务器还在线吗”"
            "“现在负载是升是降”“还是原来那几台吗/有没有新增”——这类必须本轮实际调用过对应工具拿最新值才可信；"
            "给了这类数据却没调相应工具，判为不通过（很可能是编造的）。\n"
            "   (b) 只是问【上文对话里已经出现过的信息】——如“第一个命令的结果是啥”“刚才那个工具返回了什么”"
            "“我最开始问的是什么”——这是在问历史，助手照上文如实复述、不重新调工具是正确的，"
            "不能因为“本轮没调工具”就判不通过。\n"
            "   关键判据：只要问句里有“现在/还剩/最新/是升是降/变没变/有没有新增”这类指向此刻真实值的成分，"
            "哪怕它同时引用了历史数据，也按 (a) 处理——不能拿历史旧值顶替、也不能用旧快照算一算就当本轮结果。\n"
            "   一句话里夹了多个小问时，逐个套 (a)/(b)：只要有任一点属于 (a) 却没在本轮调对应工具，就判不通过。\n"
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

    # 已到质检上限，route_after_evaluate 会直接 END、不会再回 agent 重做——这时候绝对不能删草稿。
    # 删了的话本轮这段回答既不在最终状态里（chat_service 落库时 turn_messages 会切成空、一条不存），
    # 前端又因为不会再收到 reset/token 而把它留在屏幕上，就成了"页面有、库里没有、刷新即消失"。
    # 所以到上限就把当前这版草稿（哪怕没通过质检）当作最终答案留下，让它照常落库。
    if review_attempts >= MAX_REVIEW_ATTEMPTS:
        return {"passed": False, "review_attempts": review_attempts}

    # 还能重试：把本轮产生的草稿（含 tool_calls / ToolMessage）整批从 messages 里删掉，
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
