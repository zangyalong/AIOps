from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.services import conversation_service, message_service


class ChatStreamEvent(TypedDict, total=False):
    type: str  # "token" | "reset" | "done" | "error"
    content: str
    conversation_id: str  # 要经 json.dumps 发给前端，uuid.UUID 必须转成字符串
    title: str
    message: str


async def _load_history_as_messages(
    db: AsyncSession, conversation_id: uuid.UUID
) -> list[BaseMessage]:
    history = await message_service.list_messages(db, conversation_id)
    return [
        HumanMessage(m.content) if m.role == "user" else AIMessage(m.content)
        for m in history
    ]


async def stream_chat_turn(
    graph: CompiledStateGraph,
    conversation_id: uuid.UUID,
    user_message: str,
    new_title: str | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """跑一轮对话：加载历史喂给图、流式吐出最终答案、结束后落库。

    不接收外部 db session：读历史+存用户消息、以及最后存回答，各自开一个短生命周期的
    session（开→用→关），中间真正流式的那段不持有任何 db 连接。这样 session 生命周期
    不会被 StreamingResponse 拖到整个流结束，比复用请求级 session 干净。

    只转发最后一次 call_model 节点产出的文字；如果反思环节把回答打回重试，会先发
    一个 reset 事件，被否决的草稿既不展示也不会存进最终答案。
    new_title 只在这是一次新对话时传入，会附带在 done 事件里，让前端更新侧栏标题。
    """
    # 第一段短事务：加载历史 + 用户消息落库。用户这句先落库——即使后面 LLM 调用失败，
    # 至少用户发过什么是有记录的。开完就关，不占着连接跑后面的流式。
    async with AsyncSessionLocal() as db:
        prior_messages = await _load_history_as_messages(db, conversation_id)
        await message_service.create_message(
            db, conversation_id, role="user", content=user_message
        )

    reflect_just_ran = False
    answer_parts: list[str] = []
    try:
        # 真正的流式：这段不持有任何 db 连接
        async for event in graph.astream_events(
            {"messages": [*prior_messages, HumanMessage(user_message)]},
            version="v2",
        ):
            kind = event["event"]
            node = event.get("metadata", {}).get("langgraph_node")

            if kind == "on_chain_end" and node == "reflect":
                reflect_just_ran = True
            elif kind == "on_chat_model_stream" and node == "call_model":
                content = event["data"]["chunk"].content
                if content:
                    if reflect_just_ran:
                        # 反思环节打回重试，前面流出去的是被否决的草稿，
                        # 清空前端已显示内容，也别把草稿存进最终答案里
                        answer_parts.clear()
                        yield {"type": "reset"}
                        reflect_just_ran = False
                    answer_parts.append(content)
                    yield {"type": "token", "content": content}

        # 第二段短事务：最终回答落库
        final_answer = "".join(answer_parts)
        async with AsyncSessionLocal() as db:
            if final_answer:
                await message_service.create_message(
                    db, conversation_id, role="assistant", content=final_answer
                )
            await conversation_service.touch_conversation(db, conversation_id)

        done_event: ChatStreamEvent = {"type": "done", "conversation_id": str(conversation_id)}
        if new_title is not None:
            done_event["title"] = new_title
        yield done_event
    except Exception as exc:  # noqa: BLE001 - 流式响应里要把错误也发给前端，不能直接 500
        yield {"type": "error", "message": str(exc)}
