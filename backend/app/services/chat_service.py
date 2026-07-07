from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
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


async def _load_history_messages(
    db: AsyncSession, conversation_id: uuid.UUID
) -> list[BaseMessage]:
    """把历史消息行还原成 LangChain 消息序列（完整轨迹），直接喂回图。

    现在历史里 assistant 是带 tool_calls 的、后面跟着 tool 结果——是"调了工具才回答"的好样例，
    不会再造成"不调工具直接吐数据"的坏样例（那正是当初改用摘要的原因，现在 tool_calls 落库了、
    这条障碍没了）。数据新鲜度靠 PERSONA 铁律兜（上文历史里的工具结果是旧快照、不能当本轮结果）。

    注意配对：带 tool_calls 的 AIMessage 后面必须紧跟对应 tool_call_id 的 ToolMessage，否则
    DeepSeek 会 400。历史行是按调用顺序落库的，逐行还原即保持这个配对，不用额外处理。
    """
    history = await message_service.list_messages(db, conversation_id)
    messages: list[BaseMessage] = []
    for m in history:
        if m.role == "user":
            messages.append(HumanMessage(m.content))
        elif m.role == "assistant":
            messages.append(AIMessage(content=m.content, tool_calls=(m.tool_calls or [])))
        elif m.role == "tool":
            messages.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id or ""))
    return messages


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

    历史以「完整真轨迹」喂回图（Human/AI带tool_calls/Tool/AI 一条条真消息），不再压成摘要。
    只转发 agent 节点产出的文字给前端做流式展示；真正落库的是本轮走完后的「最终图状态」里
    本轮那段轨迹（最后一条 HumanMessage 之后的消息——它之前都是历史、已经在库里，别重复存），
    按 LangChain 消息类型逐条存成 assistant / tool 行。Reflexion 打回的重试已被图内 RemoveMessage
    从状态里剪掉，所以最终状态里本轮那段天然就是「通过的那次」，落库不用自己去筛。
    如果质检打回重试，会先发一个 reset 事件，让前端把被否决的草稿整段清掉。
    new_title 只在这是一次新对话时传入，会附带在 done 事件里，让前端更新侧栏标题。
    """
    # 第一段短事务：加载历史（还原成真消息序列）+ 用户消息落库。用户这句先落库——即使后面
    # LLM 调用失败，至少用户发过什么是有记录的。开完就关，不占着连接跑后面的流式。
    async with AsyncSessionLocal() as db:
        history_messages = await _load_history_messages(db, conversation_id)
        await message_service.create_message(
            db, conversation_id, role="user", content=user_message
        )

    evaluate_just_ran = False
    final_state: dict | None = None
    try:
        # 真正的流式：这段不持有任何 db 连接
        async for event in graph.astream_events(
            {
                "messages": [*history_messages, HumanMessage(user_message)],
                "tool_iterations": 0,
                "reflections": [],
                "review_attempts": 0,
                "force_tools": False,
            },
            version="v2",
        ):
            kind = event["event"]
            node = event.get("metadata", {}).get("langgraph_node")

            # 顶层图跑完的 on_chain_end（parent_ids 为空）带着最终状态，里面 messages 就是要落库的
            # 干净轨迹。放最后取，覆盖式更新即可。
            if kind == "on_chain_end" and event.get("parent_ids") == []:
                final_state = event["data"].get("output")
                continue

            # 工具调用的开始/结束：只转发我们自己那几个运维工具（TOOLS_BY_NAME 兜底过滤，
            # 顺带挡掉 evaluate 结构化输出那个内部 tool_call），让前端看到 agent 在查/执行什么。
            # start 和 end 带同一个 run_id，前端据此把对应工具从"进行中"标记成"完成"。
            if kind == "on_tool_start" and event["name"] in TOOLS_BY_NAME:
                if evaluate_just_ran:
                    # 重试的第一个动作可能是"调工具"（比如 IP/状态类被旧值答错、打回后强制调工具的场景），
                    # 这条 tool_start 会比 agent 的第一个 token 先到。reset 必须赶在它之前发，否则前端会
                    # 先画出这次重试的工具卡片、紧接着又被 reset 连着旧草稿一起清掉——于是实时看不到工具
                    # 调用、只看到结果，可刷新后（从落库的 tool_calls 重建）工具卡片又冒出来，两边对不上。
                    yield {"type": "reset"}
                    evaluate_just_ran = False
                yield {"type": "tool_start", "id": event["run_id"], "tool": event["name"]}
            elif kind == "on_tool_end" and event["name"] in TOOLS_BY_NAME:
                yield {"type": "tool_end", "id": event["run_id"]}
            elif kind == "on_chain_end" and node == "evaluate":
                # 质检跑完了。如果它打回重试，接下来会有新一轮 agent/工具活动——用这个标记，等重试的
                # 第一个"可见动作"（工具调用 tool_start，或 agent 的第一个 token，谁先到算谁）到了再发
                # reset（质检通过时不会再有这些活动，就不会发，通过的答案不会被误清）。
                evaluate_just_ran = True
            elif kind == "on_chat_model_stream" and node == "agent":
                content = event["data"]["chunk"].content
                if content:
                    if evaluate_just_ran:
                        # 重试开始且第一个可见动作是文字（这次重试没先调工具）：被否决那一整次
                        # （旁白+工具+答案）都作废，前端整段清掉重来
                        yield {"type": "reset"}
                        evaluate_just_ran = False
                    yield {"type": "token", "content": content}

        # 第二段短事务：把最终状态里「本轮」那段轨迹按消息类型逐条落库。历史现在也在 messages 里，
        # 只取最后一条 HumanMessage（当前问题，已在开头存过）之后的部分——它之前都是历史、库里已有，
        # 不能重复存。每条单独 commit，靠各自不同的 created_at 保证顺序（顺序 await、事务开始时间递增）。
        all_messages = (final_state or {}).get("messages", [])
        last_human = max(
            (i for i, m in enumerate(all_messages) if isinstance(m, HumanMessage)), default=-1
        )
        turn_messages = all_messages[last_human + 1 :]
        async with AsyncSessionLocal() as db:
            for msg in turn_messages:
                if isinstance(msg, AIMessage):
                    await message_service.create_message(
                        db,
                        conversation_id,
                        role="assistant",
                        content=str(msg.content),
                        tool_calls=(msg.tool_calls or None),
                    )
                elif isinstance(msg, ToolMessage):
                    await message_service.create_message(
                        db,
                        conversation_id,
                        role="tool",
                        content=str(msg.content),
                        tool_call_id=msg.tool_call_id,
                    )
                # turn_messages 已切在最后一条 HumanMessage 之后，只会是 assistant / tool
            await conversation_service.touch_conversation(db, conversation_id)

        done_event: ChatStreamEvent = {"type": "done", "conversation_id": str(conversation_id)}
        if new_title is not None:
            done_event["title"] = new_title
        yield done_event
    except Exception as exc:  # noqa: BLE001 - 流式响应里要把错误也发给前端，不能直接 500
        yield {"type": "error", "message": str(exc)}
