from __future__ import annotations

import time
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field
from functools import lru_cache

from app.agents.tools.servers import query_servers
from app.core.config import settings

TOOLS = [query_servers]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

# 反思环节最多重试几次，防止“一直觉得不满意”死循环
MAX_ATTEMPTS = 2

PERSONA = "你是 AIOMS 的运维助手。可用工具：query_servers（查询 servers 表）。"


class ReflectionVerdict(BaseModel):
    satisfied: bool = Field(description="助手的回答是否已经完整回应了用户这一轮的问题")
    feedback: str = Field(description="不满意时说明还缺什么；满意时留空字符串")


class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    plan: str
    satisfied: bool
    attempts: int

@lru_cache(maxsize=None)
def _llm(bind_tools: bool) -> ChatOpenAI:
    llm = ChatOpenAI(
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        # 关掉思考模式：一是 reflect 节点用结构化输出会强制 tool_choice，思考模式下
        # DeepSeek 直接 400；二是思考过程本身要多生成一截 token，图里一次对话最多要串行
        # 跑 3-7 次 LLM 调用，思考模式叠加下来明显拖慢响应。
        extra_body={"thinking": {"type": "disabled"}},
    )
    return llm.bind_tools(TOOLS) if bind_tools else llm


def _latest_human_content(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


async def plan_node(state: ChatState) -> dict:
    """先想一遍打算怎么答，不直接回答用户。"""
    t0 = time.monotonic()
    prompt = [
        SystemMessage(
            PERSONA
            + "现在请你先规划：用一两句话说明打算怎么回答用户最新的问题，"
            "需不需要用 query_servers 查一下。只说计划，不要直接回答。"
        ),
        *state["messages"],
    ]
    response = await _llm(bind_tools=False).ainvoke(prompt)
    print(f"[graph] plan_node 耗时 {time.monotonic() - t0:.1f}s，plan={response.content!r}")
    return {"plan": str(response.content), "attempts": 0}


async def call_model_node(state: ChatState) -> dict:
    """按当前计划执行：可能直接回答，也可能先调用工具。"""
    t0 = time.monotonic()
    prompt = [
        SystemMessage(f"{PERSONA}\n当前计划：{state['plan']}"),
        *state["messages"],
    ]
    response = await _llm(bind_tools=True).ainvoke(prompt)
    print(
        f"[graph] call_model_node 耗时 {time.monotonic() - t0:.1f}s，"
        f"tool_calls={response.tool_calls!r}"
    )
    return {"messages": [response]}


async def call_tools_node(state: ChatState) -> dict:
    t0 = time.monotonic()
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    results = []
    for tool_call in last.tool_calls:
        selected_tool = TOOLS_BY_NAME[tool_call["name"]]
        result = await selected_tool.ainvoke(tool_call["args"])
        results.append(ToolMessage(content=result, tool_call_id=tool_call["id"]))
    print(f"[graph] call_tools_node 耗时 {time.monotonic() - t0:.1f}s")
    return {"messages": results}


async def reflect_node(state: ChatState) -> dict:
    """检查最新回答是否已经完整回应了用户这一轮的问题；不满意就把缺什么写成新计划。

    用结构化输出（Pydantic model）拿判断结果，而不是让 LLM 严格照抄 "SATISFIED" 这个词再
    做字符串匹配——实测对着"你好"这种简单寒暄，模型不会每次都精确回复约定的关键词，
    字符串匹配会误判成"不满意"，白白多重试好几轮，拖慢响应。
    """
    t0 = time.monotonic()
    question = _latest_human_content(state["messages"])
    answer = str(state["messages"][-1].content)
    prompt = [
        SystemMessage("你是质检模块，判断下面的回答是否完整回应了用户的问题。"),
        HumanMessage(f"用户问题：{question}\n助手回答：{answer}"),
    ]
    # method="function_calling"：DeepSeek 现在不支持 with_structured_output 默认走的
    # response_format=json_schema 严格模式（400 This response_format type is unavailable
    # now），改成走已经验证过能用的 tool-calling 机制实现结构化输出。
    structured_llm = _llm(bind_tools=False).with_structured_output(
        ReflectionVerdict, method="function_calling"
    )
    verdict = await structured_llm.ainvoke(prompt)
    attempts = state["attempts"] if verdict.satisfied else state["attempts"] + 1
    plan = "" if verdict.satisfied else verdict.feedback
    print(f"[graph] reflect_node 耗时 {time.monotonic() - t0:.1f}s，verdict={verdict!r}")
    return {"plan": plan, "satisfied": verdict.satisfied, "attempts": attempts}


def route_after_model(state: ChatState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "call_tools"
    return "reflect"


def route_after_reflect(state: ChatState) -> str:
    if state["satisfied"] or state["attempts"] >= MAX_ATTEMPTS:
        return END
    return "call_model"


def build_chat_graph() -> CompiledStateGraph:
    # 不传 checkpointer：多轮对话的持久化现在自己用 messages 表管（每轮把完整历史
    # 当输入喂给图），图本身只在一次 ainvoke/astream_events 调用内部跑，
    # reflect -> call_model 这个环不需要 checkpointer 也能正常执行。
    builder = StateGraph(ChatState)
    builder.add_node("plan", plan_node)
    builder.add_node("call_model", call_model_node)
    builder.add_node("call_tools", call_tools_node)
    builder.add_node("reflect", reflect_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "call_model")
    builder.add_conditional_edges("call_model", route_after_model)
    builder.add_edge("call_tools", "call_model")
    builder.add_conditional_edges("reflect", route_after_reflect)

    return builder.compile()
