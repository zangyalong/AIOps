from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graphs.chat_graph import TOOLS_BY_NAME
from app.db.session import AsyncSessionLocal
from app.services import conversation_service, message_service


class ChatStreamEvent(TypedDict, total=False):
    type: str  # "token" | "reset" | "tool_start" | "tool_end" | "done" | "error"
    content: str
    conversation_id: str  # 要经 json.dumps 发给前端，uuid.UUID 必须转成字符串
    title: str
    message: str
    tool: str  # tool_start 时带上工具名，前端渲染成"正在调用 XXX"
    id: str  # tool_start / tool_end 用同一个 run_id 配对，前端据此把某个工具标记成已完成


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

    只转发 agent 节点产出的文字（agent ⇄ tools 循环里可能有好几段，最终答案是不再带
    tool_calls 的那一段；中间如果模型边调工具边说话，那些文字也会流给前端，属于正常过程）。
    如果 Reflexion 质检把回答打回重试，会先发一个 reset 事件，被否决的草稿既不展示也不入库。
    new_title 只在这是一次新对话时传入，会附带在 done 事件里，让前端更新侧栏标题。
    """
    # 第一段短事务：加载历史 + 用户消息落库。用户这句先落库——即使后面 LLM 调用失败，
    # 至少用户发过什么是有记录的。开完就关，不占着连接跑后面的流式。
    async with AsyncSessionLocal() as db:
        prior_messages = await _load_history_as_messages(db, conversation_id)
        await message_service.create_message(
            db, conversation_id, role="user", content=user_message
        )

    evaluate_just_ran = False
    answer_parts: list[str] = []
    try:
        # 真正的流式：这段不持有任何 db 连接
        async for event in graph.astream_events(
            {
                "messages": [*prior_messages, HumanMessage(user_message)],
                "tool_iterations": 0,
                "reflections": [],
                "review_attempts": 0,
            },
            version="v2",
        ):
            kind = event["event"]
            node = event.get("metadata", {}).get("langgraph_node")

            # 工具调用的开始/结束：只转发我们自己那几个运维工具（TOOLS_BY_NAME 兜底过滤，
            # 顺带挡掉 evaluate 结构化输出那个内部 tool_call），让前端看到 agent 在查/执行什么。
            # start 和 end 带同一个 run_id，前端据此把对应工具从"进行中"标记成"完成"。
            if kind == "on_tool_start" and event["name"] in TOOLS_BY_NAME:
                # agent 在调工具前可能先说一句（"我查一下…"），那属于中间思考、不是最终
                # 答案——工具一开始执行就把之前攒的文字清掉，final_answer 只留看完工具
                # 结果后 agent 产出的最后一段。（这些中间文字已经流给前端了，属于正常过程）
                answer_parts.clear()
                yield {"type": "tool_start", "id": event["run_id"], "tool": event["name"]}
            elif kind == "on_tool_end" and event["name"] in TOOLS_BY_NAME:
                yield {"type": "tool_end", "id": event["run_id"]}
            elif kind == "on_chain_end" and node == "evaluate":
                # 质检跑完了。如果它打回重试，接下来 agent 会重新流式吐答案——用这个标记，
                # 等重试的第一个 token 到了再发 reset（质检通过时不会再有 agent token，就不会发）。
                evaluate_just_ran = True
            elif kind == "on_chat_model_stream" and node == "agent":
                content = event["data"]["chunk"].content
                if content:
                    if evaluate_just_ran:
                        # 重试开始：前面流出去的是被否决的草稿，让前端清掉，也别入库
                        answer_parts.clear()
                        yield {"type": "reset"}
                        evaluate_just_ran = False
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
