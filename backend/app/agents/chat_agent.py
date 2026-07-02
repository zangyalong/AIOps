from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from app.agents.tools.servers import query_servers
from app.core.config import settings

TOOLS = [query_servers]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SYSTEM_PROMPT = (
    "你是 AIOMS 的运维助手。如果用户问的是服务器相关信息，"
    "先用 query_servers 工具查一遍再回答；其他问题正常聊天即可。"
)


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
    ).bind_tools(TOOLS)


async def chat(user_message: str) -> str:
    llm = _build_llm()
    messages = [SystemMessage(SYSTEM_PROMPT), HumanMessage(user_message)]

    ai_message = await llm.ainvoke(messages)
    messages.append(ai_message)

    # 阶段3先手动写这个“调用工具 -> 把结果喂回去 -> 再问一次”的循环，
    # 阶段4引入 LangGraph 后会把这段改成状态机，现在先让流程可见、好懂。
    if not ai_message.tool_calls:
        return ai_message.content

    for tool_call in ai_message.tool_calls:
        selected_tool = TOOLS_BY_NAME[tool_call["name"]]
        result = await selected_tool.ainvoke(tool_call["args"])
        messages.append(ToolMessage(content=result, tool_call_id=tool_call["id"]))

    final_message = await llm.ainvoke(messages)
    return final_message.content
